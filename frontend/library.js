(() => {
  "use strict";

  const QB = window.QBRender;
  const workspace = window.LibraryWorkspace;
  const solutions = window.LibrarySolutions;
  const $ = (id) => document.getElementById(id);
  const ui = {
    search: $("searchInput"), source: $("sourceSelect"), types: $("typeFilters"),
    status: $("libraryStatus"), list: $("libraryList"), more: $("moreButton"),
    inspector: $("questionInspector"), inspectorContent: $("questionInspectorContent"), inspectorToggle: $("inspectorToggle"),
    basketButton: $("basketButton"), basketCount: $("basketCount"),
    sourceDialog: $("sourceDialog"), sourceTitle: $("sourceTitle"), sourcePages: $("sourcePages"), sourceReviewLink: $("sourceReviewLink"),
    sourceQuestion: $("sourceQuestion"), sourceWholePage: $("sourceWholePage"), sourceZoomOut: $("sourceZoomOut"),
    sourceZoomIn: $("sourceZoomIn"), sourceZoom: $("sourceZoom"), sourceFit: $("sourceFit"), sourceRelated: $("sourceRelated"),
    sheet: $("printSheet"), paper: $("printPaper"), printTitle: $("printTitle"), printAnswers: $("printAnswers"),
    printOrigin: $("printOrigin"), printAi: $("printAiAnswers"), printAiBox: $("printAiBox"),
    answerFilters: $("answerFilters"), tagBox: $("tagFilterBox"), tagSelect: $("tagSelect"), extraTools: $("tagQuickTools"), batchTools: $("aiBatchTools")
  };
  const params = new URL(window.location.href).searchParams;
  // 筛选栏里题型固定的排列顺序。题库里没有的也要占一个位置（数字 0、置灰），
  // 免得另一个筛选把结果清空时，整排按钮跟着一起消失。
  const FILTER_TYPES = ["single_choice", "multiple_choice", "fill_blank", "true_false", "free_response"];
  const state = {
    q: params.get("q") || "",
    document: params.get("document") || "",
    type: params.get("type") || "",
    // human = 人对照原卷核对过；ai = AI 助手审核入库，还没人工核对。
    review: ["human", "ai"].includes(params.get("review")) ? params.get("review") : "",
    answer: ["yes", "no"].includes(params.get("answer")) ? params.get("answer") : "",
    tag: params.get("tag") || "",
    tagAll: params.get("tag_all") || "",
    tagAny: params.get("tag_any") || "",
    tagNot: params.get("tag_not") || "",
    tagMode: ["all", "any", "not"].includes(params.get("tag_mode")) ? params.get("tag_mode") : "all",
    untaggedKnowledge: params.get("untagged_knowledge") === "true",
    tagReview: params.get("tag_review") === "true",
    sort: params.get("sort") === "source" ? "source" : "recent",
    view: "all",
    selected: new Set(),
    expanded: new Set(),
    catalog: new Map(),
    basketMissing: [],
    basketToken: 0,
    basketLoading: false,
    // 右边缘那个篮抽屉展开没有。默认收起，只留一条把手。
    basketVisible: false,
    draft: null,
    draftDirty: false,
    draftBaseline: null,
    // 1.10.5: 翻开了答案的题（每题单独翻开，不再一键全部展开）。
    opened: new Set(),
    features: {},
    ai: { mode: "assistant" },
    poll: null,
    focus: params.get("focus") || "",
    inspectorId: params.get("focus") || "",
    items: [],
    retainedTagItems: new Map(),
    total: 0,
    facets: null,
    tagWork: { untagged_knowledge: 0, pending_review: 0 },
    loading: false,
    token: 0,
    basket: loadBasket()
  };
  // 筛选刷新会重建这个区域，但展开状态应在当前浏览过程中保留。
  let aiBatchToolsOpen = false;

  function node(tag, className = "", text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function iconButton(tag, className, label, iconName) {
    const element = node(tag, className);
    if (iconName) element.append(icon(iconName));
    element.append(document.createTextNode(label));
    if (tag === "button") element.type = "button";
    return element;
  }

  // 提示条放进浏览器顶层（popover），对话框打开时也能看见。
  let toastTimer = null;
  function toast(message, kind = "") {
    const box = $("toast");
    if (!box) return;
    box.replaceChildren(node("span", "toast-text", message));
    box.className = `toast ${kind}`.trim();
    const show = (open) => {
      if (typeof box.showPopover === "function") {
        try { if (box.matches(":popover-open")) box.hidePopover(); if (open) box.showPopover(); } catch { /* 已关闭 */ }
      } else box.hidden = !open;
    };
    show(true);
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => show(false), kind === "error" ? 6000 : 3500);
  }

  function confirmDialog({ title, text = "", ok = "确定", danger = false, focusCancel = false }) {
    const dialog = $("confirmDialog");
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmText").hidden = !text;
    $("confirmOk").textContent = ok;
    $("confirmIconUse").setAttribute("href", danger ? "#i-alert" : "#i-question");
    dialog.classList.toggle("danger", danger);
    dialog.returnValue = "";
    dialog.showModal();
    (focusCancel ? dialog.querySelector('[value="cancel"]') : $("confirmOk")).focus();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    });
  }

  function loadBasket() {
    try {
      const value = JSON.parse(window.localStorage.getItem("qb-basket") || "[]");
      return workspace.uniqueIds(value);
    } catch {
      return [];
    }
  }

  // 1.13.4：把手的荧光在用户第一次展开篮子之后就永久停掉。存在本机，
  // 和试题篮同一个作用域；存不住时退化成「这次还会闪」。
  const BASKET_HANDLE_SEEN_PREF = "qb-basket-handle-seen";
  function basketHandleSeen() {
    try { return window.localStorage.getItem(BASKET_HANDLE_SEEN_PREF) === "1"; } catch { return false; }
  }
  function markBasketHandleSeen() {
    try { window.localStorage.setItem(BASKET_HANDLE_SEEN_PREF, "1"); } catch { /* 只在本页有效 */ }
    document.body.classList.add("library-basket-seen");
  }

  function saveBasket() {
    prunePrintChoices();
    try { window.localStorage.setItem("qb-basket", JSON.stringify(state.basket)); } catch { /* 浏览器禁止存储时，试题篮只在本页有效 */ }
    ui.basketCount.textContent = String(state.basket.length);
    ui.basketButton.hidden = state.basket.length === 0;
    ui.basketButton.classList.remove("bump");
    void ui.basketButton.offsetWidth;
    ui.basketButton.classList.add("bump");
    if (state.draft || state.draftDirty) markDraftDirty();
    else if (state.draftBaseline !== null) state.draftBaseline = draftSignature();
    renderBasket();
    syncSelection();
    void refreshBasket();
  }

  function syncUrl() {
    const url = new URL(window.location.href);
    for (const [key, value] of [["q", state.q], ["document", state.document], ["type", state.type], ["review", state.review],
      ["answer", state.answer], ["tag", state.tag], ["tag_all", state.tagAll], ["tag_any", state.tagAny],
      ["tag_not", state.tagNot], ["tag_mode", state.tagMode === "all" ? "" : state.tagMode],
      ["untagged_knowledge", state.untaggedKnowledge ? "true" : ""], ["tag_review", state.tagReview ? "true" : ""]]) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    if (state.sort === "source") url.searchParams.set("sort", "source");
    else url.searchParams.delete("sort");
    url.searchParams.delete("focus");
    window.history.replaceState(null, "", url);
  }

  function libraryQuery(filters = state, { limit = 40, offset = 0 } = {}) {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset), sort: filters.sort || "recent" });
    const aliases = { tag_all: "tagAll", tag_any: "tagAny", tag_not: "tagNot", tag_mode: "tagMode", untagged_knowledge: "untaggedKnowledge", tag_review: "tagReview" };
    for (const key of ["q", "document", "type", "review", "answer", "tag", "tag_all", "tag_any", "tag_not", "tag_mode", "untagged_knowledge", "tag_review"]) {
      const raw = filters[key] ?? filters[aliases[key]];
      const value = key === "q" ? String(raw || "").trim() : raw;
      if (value) query.set(key, value);
    }
    return query;
  }

  function markLibraryResult(query = null) {
    if (query) state.resultQuery = query.toString();
    const filters = new URLSearchParams(state.resultQuery || ""); filters.delete("limit"); filters.delete("offset");
    const identity = JSON.stringify([state.view, state.view === "selected" ? state.basket : filters.toString(), visibleItems().map(item => item.id)]);
    if (identity !== state.resultIdentity) { state.resultGeneration = (state.resultGeneration || 0) + 1; state.resultIdentity = identity; }
  }

  async function load({ append = false, quiet = false } = {}) {
    if (!quiet && !append) { state.retainedTagItems.clear(); closeTagHover(); }
    if (state.view === "selected") {
      window.clearTimeout(state.poll);
      try {
        const response = await fetch("/api/settings/library-ai", { cache: "no-store" });
        if (response.ok) {
          const settings = await response.json();
          state.features = { ...state.features, ...settings.features };
          state.ai = { mode: settings.mode, provider: settings.provider, message: settings.message, api_ready: settings.api_ready };
        }
      } catch (_) { /* Keep the last known settings when a local refresh fails. */ }
      await refreshBasket({ force: true });
      markLibraryResult();
      render();
      scheduleJobRefresh();
      return;
    }
    const token = ++state.token;
    state.loading = true;
    $("libraryLoadError").hidden = true;
    window.clearTimeout(state.poll);
    // A quiet refresh (jobs finishing) reloads everything already shown, page by page.
    const wanted = quiet ? Math.max(40, state.items.length) : 40;
    const query = libraryQuery(state, { limit: Math.min(100, wanted), offset: append ? state.items.length : 0 });
    if (!append && !quiet) setStatus("正在读取题库…");
    try {
      const response = await fetch(`/api/library?${query}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "读取题库失败");
      while (quiet && body.items.length < Math.min(wanted, body.total)) {
        query.set("offset", String(body.items.length));
        const more = await fetch(`/api/library?${query}`, { cache: "no-store" });
        const page = await more.json();
        if (!more.ok || !page.items.length) break;
        body.items = body.items.concat(page.items);
      }
      if (token !== state.token) return;
      state.total = body.total;
      state.facets = body.facets;
      state.tagWork = body.tag_work || state.tagWork;
      state.features = body.features || {};
      state.ai = body.ai || { mode: "assistant" };
      state.items = append ? state.items.concat(body.items) : body.items;
      body.items.forEach(item => state.retainedTagItems.delete(item.id));
      markLibraryResult(query);
      state.items.forEach((item) => state.catalog.set(item.id, item));
      render();
      // 助手任务只等待工具写回；API 任务由后台处理。两种结果都刷新显示。
      scheduleJobRefresh();
    } catch (error) {
      if (token === state.token) {
        setStatus(state.items.length ? "当前显示上一次读取的结果，尚未应用这次筛选。" : "题库尚未读取成功。", "warn");
        const box = $("libraryLoadError");
        const retry = node("button", "button button-small", "重试读取");
        retry.id = "retryLibraryLoad";
        retry.type = "button";
        retry.addEventListener("click", () => load({ append, quiet }));
        box.replaceChildren(node("span", "", error.message || "读取题库失败。"), retry);
        box.hidden = false;
        ui.more.hidden = true;
      }
    } finally {
      if (token === state.token) state.loading = false;
    }
  }

  function scheduleJobRefresh() {
    const pending = visibleItems().flatMap((item) => item.job_details || [])
      .filter((job) => job.kind === "tags" ? true : state.features.ai_answer);
    if (pending.length) state.poll = window.setTimeout(() => load({ quiet: true }),
      pending.some((job) => job.executor === "api") ? 4000 : 10000);
  }

  function renderFacets() {
    const facets = state.facets || { sources: [], types: {} };
    const current = state.document;
    ui.source.replaceChildren(new Option("全部试卷", ""));
    // 1.12.6：同一份卷录过两次时，两条来源在题库里同名，光看文件名和题数分不出
    // 哪份是哪份 —— 照着下拉随手一选就可能撤错那一份。补上录入时间。
    const nameTotals = new Map();
    facets.sources.forEach((source) => nameTotals.set(source.filename, (nameTotals.get(source.filename) || 0) + 1));
    facets.sources.forEach((source) => {
      const suffix = nameTotals.get(source.filename) > 1 ? `，录于 ${shortDate(source.first_published_at)}` : "";
      ui.source.append(new Option(`${source.filename}（${source.count} 题${suffix}）`, source.document_id || ""));
    });
    ui.source.value = current;
    if (ui.source.value !== current) ui.source.value = "";
    if (current && !facets.sources.some((source) => source.document_id === current)) {
      ui.source.append(new Option("当前来源（此条件下无题目）", current));
      ui.source.value = current;
    }
    $("sortSelect").value = state.sort;
    ui.types.replaceChildren();
    const total = Object.values(facets.types).reduce((sum, value) => sum + value, 0);
    // 服务端给的分面是「当前筛选之后」的。所以在搜索或标签筛不出东西时，分面会整个
    // 空掉 —— 题型按钮会跟着全部消失，只剩一个「全部题型 0」，答题/审核分组整个不见，
    // 「更多筛选」也跟着没了。用户只是敲错了一个词，整条筛选栏看上去像坏掉了。
    // 题型是固定的五类，一律留着；没有的那一档置灰但仍然看得见。
    const typeKeys = [...FILTER_TYPES,
      ...Object.keys(facets.types).filter((key) => !FILTER_TYPES.includes(key)),
      ...(state.type ? [state.type] : [])]
      .filter((key, index, all) => all.indexOf(key) === index);
    [["", "全部题型", total], ...typeKeys.map((key) => [key, QB.TYPE_NAMES[key] || key, facets.types[key] || 0])]
      .forEach(([key, label, count]) => {
        const button = node("button", `draft-filter${state.type === key ? " active" : ""}`);
        button.append(node("span", "", label), node("span", "count", count));
        button.type = "button";
        button.setAttribute("aria-pressed", String(state.type === key));
        // 当前这一档是 0 时不能置灰 —— 那正是用户想取消掉的。
        // 置灰写成标记、由结尾统一套用，不要在这里就直接设 disabled：结尾还要
        // 把「已选题」的锁整体套上去，从 disabled 反推会把上一次锁过的控件永久锁死。
        if (!count && state.type !== key) {
          button.dataset.emptyFacet = "1";
          button.title = `当前筛选下没有${label}`;
        }
        button.addEventListener("click", () => { state.type = key; syncUrl(); load(); });
        ui.types.append(button);
      });
    // 一个筛选把结果清空时，别把「这里本来还有什么」也一起收走：这时候保留
    // 全部分组（数字如实是 0），用户才看得见题库里还有哪些东西可筛。
    const emptied = !(Number(state.total) || 0);
    renderReviewFilters(facets.reviews || {}, emptied);
    renderAnswerFilters(facets.answers || {}, emptied);
    renderTagFilter(facets.tags || []);
    renderExtraTools();
    // 「更多筛选」里三组都空时，点开了也是一片空白 —— 那就不出现。
    // 只能拿这三组判断：标签待办入口已经挪到折叠板外面常驻，它是「有活要干」
    // 的提示，跟着这组一起隐藏就等于有活也不吭声（1.25.0 之前就是这样丢的）。
    $("advancedFilters").hidden = [ui.answerFilters, $("reviewFilters"), ui.tagBox]
      .every((box) => box.hidden);
    const selectedView = state.view === "selected";
    // 「已选题」视图下所有筛选都要锁住。但上面刚刚标了「0 条」的那些不能一起解锁 ——
    // 这一行以前会把它们全部改回可点，置灰等于没做。
    const controls = [ui.search, ui.source, $("sortSelect"),
      ...document.querySelectorAll("#typeFilters button, .library-advanced button, .library-advanced select")];
    // 用标记而不是 disabled 反推：从 disabled 反推读的是上一次渲染留下的值，
    // 「已选题」锁过的搜索框和来源就再也解不开了。
    for (const control of controls) control.disabled = selectedView || control.dataset.emptyFacet === "1";
    for (const control of document.querySelectorAll(".library-extra-tools button")) control.disabled = selectedView;
    applyBatchButtonState();
  }

  // 「AI 批量处理」里每个按钮能不能点、排队时显示什么，都只由这一处说了算。
  // 排队时统一禁用、结束后恢复，都走同一条判据 —— 各自写一遍的话，恢复时会把
  // 本来就不该能点的按钮（比如没配 API、当前筛选为空）顺手放开。
  //
  // 「排队时显示什么」也必须放在这里，不能在点下去那一刻改一下按钮就算完：
  // 登记要好几秒，这期间界面会自己重画（题数回来了、定时刷新），按钮节点会被
  // 换成新的。原来只把「正在排队…」写在那一个节点上，一重画就没了 —— 按钮重新
  // 变回原名却仍然点不动，正是「点了以为卡死」的老样子，白改。
  let queueBusy = false;
  let queueCount = 0;
  // 刚才点的是哪一个 —— 认的是这个，不是那个节点。节点重画一次就没了，名字里
  // 还带着题数、重画后也可能变，只有这个标记是渲染时就写死在按钮上的。
  // 认错了的后果很具体：两个按钮一起显示「正在排队…」，用户会以为排了两队。
  let queueAction = "";
  // 按钮的文字是 iconButton 追加的一个文本节点，换文案时只能换它，
  // 直接改 textContent 会把图标一起抹掉。
  function queueLabelNode(control) {
    return Array.from(control.childNodes || []).find((child) => child.nodeType === 3) || null;
  }
  function markQueueing(control) {
    const label = queueLabelNode(control);
    if (!label) return;
    // 原话先存一份，结束后照原样还回去。重画出来的新节点本来就没有这份记录，
    // 存的也一定是它自己刚渲染出来的名字。
    if (!control.dataset.queueOriginal) control.dataset.queueOriginal = label.nodeValue;
    label.nodeValue = "正在排队…";
    control.setAttribute("aria-busy", "true");
  }
  function unmarkQueueing(control) {
    const label = queueLabelNode(control);
    if (label && control.dataset.queueOriginal) label.nodeValue = control.dataset.queueOriginal;
    delete control.dataset.queueOriginal;
    control.removeAttribute("aria-busy");
  }
  function applyBatchButtonState() {
    const selectedView = state.view === "selected";
    for (const control of document.querySelectorAll("#aiBatchTools button")) {
      control.disabled = queueBusy || selectedView || control.dataset.disabledInSelectedView === "true";
      // 排队期间要改名的只有刚才被点的那一个；别的按钮保持自己的名字，
      // 只是被锁住 —— 让它们也跟着说「正在排队」是在骗用户，以为他排了两队。
      if (queueBusy && control.dataset.queueAction === queueAction) markQueueing(control);
      else unmarkQueueing(control);
    }
  }

  // 有没有答案：挑出原卷没给答案的题（补答案或做 AI 参考答案时用）。
  function renderAnswerFilters(answers, emptied = false) {
    const box = ui.answerFilters;
    const yes = Number(answers.yes) || 0;
    const no = Number(answers.no) || 0;
    // emptied：别的筛选把结果清空了。这时候保留整组（数字如实是 0），
    // 用户才知道「有答案」这一档本来存在、只是这次一道都没有。
    box.hidden = !emptied && !no && !state.answer;
    box.replaceChildren();
    if (box.hidden) return;
    [["", "全部", yes + no], ["yes", "有答案", yes], ["no", "无答案", no]].forEach(([key, label, count]) => {
      const button = node("button", `draft-filter${state.answer === key ? " active" : ""}`);
      button.append(node("span", "", label), node("span", "count", count));
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.answer === key));
      if (!count && state.answer !== key) {
        button.dataset.emptyFacet = "1";
        button.title = `当前筛选下没有「${label}」的题`;
      }
      button.addEventListener("click", () => { state.answer = key; syncUrl(); load(); });
      box.append(button);
    });
  }

  function renderTagFilter(tags) {
    ui.tagBox.hidden = !(tags.length || state.tagAll || state.tagAny || state.tagNot || state.tag);
    if (ui.tagBox.hidden) return;
    const selected = String(state.tagMode === "not" ? state.tagNot : state.tagMode === "any" ? state.tagAny : state.tagAll).split(",").filter(Boolean);
    ui.tagSelect.replaceChildren();
    tags.forEach((item) => { const option = new Option(`${item.tag}（${item.count}）`, item.id); option.selected = selected.includes(item.id); ui.tagSelect.append(option); });
    selected.forEach((id) => { if (!Array.from(ui.tagSelect.options).some((option) => option.value === id)) ui.tagSelect.add(new Option("已选标签 · " + id.slice(0, 8), id, true, true)); });
    if (state.tag) ui.tagSelect.append(new Option(state.tag, state.tag));
    const mode = $("tagMatchMode"); if (mode) mode.value = state.tagMode;
    renderSavedTagFilters();
  }

  function savedTagFilters() {
    try { const values = JSON.parse(localStorage.getItem("qb-library-saved-tag-filters") || "[]"); return Array.isArray(values) ? values.filter((item) => item && typeof item.name === "string" && item.filter) : []; }
    catch (_) { return []; }
  }
  function renderSavedTagFilters() {
    const box = $("tagSavedFilterBox"), select = $("tagSavedFilterSelect"); if (!box || !select) return;
    const saved = savedTagFilters(); box.hidden = ui.tagBox.hidden;
    const current = select.value; select.replaceChildren(new Option("选择常用筛选", ""));
    saved.forEach((item, index) => select.add(new Option(item.name, String(index))));
    if (saved.some((_item, index) => String(index) === current)) select.value = current;
    $("deleteTagFilter").disabled = !select.value;
  }
  function currentTagFilter() {
    return { mode: state.tagMode, all: state.tagAll, any: state.tagAny, not: state.tagNot, legacy: state.tag };
  }
  function applySavedTagFilter(filter) {
    state.tagMode = filter.mode || "all"; state.tagAll = filter.all || ""; state.tagAny = filter.any || "";
    state.tagNot = filter.not || ""; state.tag = filter.legacy || "";
    state.untaggedKnowledge = false; state.tagReview = false; syncUrl(); load();
  }

  // 普通待处理入口常驻在筛选栏上；会调用 API 的批量任务放进单独折叠区。
  // 两者都是按钮，靠「要不要花钱」和「要不要点开」区分，不靠藏起来。
  function renderExtraTools() {
    const box = ui.extraTools;
    box.replaceChildren();
    const batchBox = ui.batchTools;
    batchBox.replaceChildren();
    const missingCount = Number(state.tagWork?.untagged_knowledge) || 0;
    const readyCount = Number(state.tagWork?.untagged_ready) || 0;
    const answerCount = Number(state.tagWork?.missing_answer) || 0;
    const queuedTags = Number(state.tagWork?.queued_tags) || 0;
    const queuedAnswers = Number(state.tagWork?.queued_answers) || 0;
    const pendingCount = Number(state.tagWork?.pending_review) || 0;
    const actionableCount = Number(state.tagWork?.pending_actionable) || 0;
    const outdatedCount = Number(state.tagWork?.outdated_suggestions) || 0;
    const apiReady = state.ai.mode === "api" && state.ai.api_ready === true;
    const tagsEnabled = state.features.knowledge_tags === true;
    const batchActions = [];
    const quickActions = [];

    // 待办入口和题型其实是同一种东西：点了就把列表筛一遍。它们以前挂着放大镜图标，
    // 一眼像「去搜索」，和下面那排纯文字的题型对不上，一屏里出现三种筛选长相。
    // 图标去掉，改成和题型同一套文字筛选；字号小一点、数字淡一点，别抢题面。
    if (pendingCount || state.tagReview) {
      const pending = iconButton("button", `button button-quiet button-small library-tool-action${state.tagReview ? " active" : ""}`,
        `AI 建议待核对 · ${pendingCount} 题`);
      // 这个数字原来把「现在就能采纳」和「旧版遗留、对不上现在的标签库」混在一起，
      // 看上去是一堆待办，点进去却几乎一步都走不了。分开说清楚，数目才不作假。
      pending.title = outdatedCount
        ? `这 ${pendingCount} 道题有 AI 建议，其中 ${actionableCount} 道现在就能采纳。`
          + `另外 ${outdatedCount} 道对不上现在的标签库，或者题目后来改过标签，需要重新生成才能采纳。`
        : `这 ${pendingCount} 道题有 AI 建议等你核对，采纳之后才会真的贴到题目上。`;
      pending.setAttribute("aria-pressed", String(state.tagReview));
      pending.addEventListener("click", () => { state.tagReview = !state.tagReview; syncUrl(); load(); });
      quickActions.push(pending);
    }
    if (missingCount || state.untaggedKnowledge) {
      const untagged = iconButton("button", `button button-quiet button-small library-tool-action${state.untaggedKnowledge ? " active" : ""}`,
        `未标知识点 · ${missingCount} 题`);
      untagged.title = `整个题库里还没打上正式知识点标签的 ${missingCount} 道题。点一下只看这些题。`;
      untagged.setAttribute("aria-pressed", String(state.untaggedKnowledge));
      untagged.addEventListener("click", () => { state.untaggedKnowledge = !state.untaggedKnowledge; syncUrl(); load(); });
      quickActions.push(untagged);
    }

    if (tagsEnabled) {
      // 按钮名先说「做什么、对谁做」，范围和代价放进悬停说明。
      // 原来两个按钮名用的是内部说法（全库 / 当前筛选这类词），看不出两条差在哪；
      // 而两个题数只差 3，光看数字更看不出来。
      //
      // 题数必须写「点下去真会排上的那一批」，不能写「还没打标签的全部」。
      // 已经在等或在跑建议的题也算没打标签，但点下去一道也排不上 ——
      // 按钮写着 805、点完什么都没发生，比不给反馈更糟。
      const tags = iconButton("button", "button button-quiet button-small library-tool-action",
        `补齐缺的知识点标签 · ${readyCount} 题`, "plus");
      tags.dataset.disabledInSelectedView = String(!apiReady || readyCount === 0);
      // 排队时要改名的只有被点的那个，靠这个标记认。名字里带着题数、重画后会变，
      // 按钮节点本身也会被换掉，只有它是渲染时就写死的。
      tags.dataset.queueAction = "tags";
      tags.title = !apiReady
        ? "需要先到「设置 → API 配置」里配置并测试通过。"
        : readyCount
          ? `整个题库里还没生成过标签建议的题，共 ${readyCount} 道。点一次让 AI 全部生成，不受上面的筛选影响。`
            + "已经在等或在生成的、以及已经等你核对的，不会重复排队。"
            + "每道题调用一次 AI，会消耗额度。生成的标签只是建议，要你逐条核对确认后才贴到题目上。"
          : `题库里 ${missingCount} 道题还没有正式知识点标签，但每一道都已经在等、在生成，或已有建议等你核对，`
            + "现在没有需要新排队的。看完上面的待核对入口，或等 AI 做完再看。";
      tags.addEventListener("click", () => {
        if (!readyCount) { toast("现在没有需要新排队的题。", ""); return; }
        confirmBulkTags(readyCount, missingCount);
      });
      batchActions.push(tags);
      const current = iconButton("button", "button button-quiet button-small library-tool-action",
        `只处理当前这批 · ${Number(state.total) || 0} 题`, "plus");
      current.title = apiReady
        ? `只处理你现在筛选出来的这 ${Number(state.total) || 0} 道题，包括还没加载到屏幕上的。`
          + "想补整个题库请用上面那条。每道题调用一次 AI，会消耗额度。"
          + "生成的标签只是建议，要你逐条核对确认后才贴到题目上。"
        : "需要先到「设置 → API 配置」里配置并测试通过。";
      current.dataset.disabledInSelectedView = String(!apiReady || Number(state.total) === 0);
      current.dataset.queueAction = "tags-current";
      current.addEventListener("click", async () => {
        const count = Number(state.total) || 0;
        const filters = currentFilterPayload();
        if (!count) { toast("当前筛选没有题目。", "error"); return; }
        const confirmed = await confirmDialog({
          title: `给当前这批 ${count} 道题生成标签建议？`,
          text: `只处理当前筛选出来的 ${count} 道题，包括还没显示在屏幕上的；想补整个题库请用上面那条。`
            + "每道题调用一次 AI，会消耗额度。生成的标签只是建议，不会直接贴到题目上。"
            + "开始之后再改筛选，也不会扩大这次的范围。",
          ok: "开始生成"
        });
        if (confirmed) queueJobs("tags", { scope: "current_filter", filters }, "tags-current", count);
      });
      batchActions.push(current);
    }
    if (state.features.ai_answer) {
      const answers = iconButton("button", "button button-quiet button-small library-tool-action",
        `补齐缺的参考答案 · ${answerCount} 题`, "plus");
      answers.dataset.disabledInSelectedView = String(!apiReady || answerCount === 0);
      answers.dataset.queueAction = "answer";
      answers.title = !apiReady
        ? "需要先到「设置 → API 配置」里配置并测试通过。"
        : answerCount
          ? `原卷里没有答案、也没有 AI 参考答案的题，共 ${answerCount} 道。点一次让 AI 全部补上，不受上面的筛选影响。`
            + "每道题调用一次 AI，会消耗额度。生成的答案标着「AI 参考 · 未核对」，和原卷答案分开存放。"
          : "题库里每一道题都已经有答案或 AI 参考答案了，不用再补。";
      answers.addEventListener("click", () => {
        if (!answerCount) { toast("题库里的答案已经齐了。", ""); return; }
        confirmBulkAnswers(answerCount);
      });
      batchActions.push(answers);
    }
    box.hidden = !quickActions.length;
    batchBox.hidden = !batchActions.length || state.view === "selected";

    // 常驻的一排小按钮，不再套一层「快速查看」标题。筛选栏里来源、题型、排序
    // 都没有小标题，按钮名本身已经说清是做什么的，多套一层只占地方；
    // 分组名留给 aria-label，读屏用户仍然听得到这一组的用途。
    box.append(...quickActions);
    if (batchActions.length) {
      const batch = node("details", "library-batch-ai");
      batch.open = aiBatchToolsOpen;
      // 入口名字原来只写「AI 批量处理」，看不出它要花钱、也不知道会动多少题，
      // 又是个贴在右边缘的小灰按钮，很容易整天没被看见。改成「AI 批量处理题目」
      // 之后还是有人问「批量处理啥呀」——名字里必须出现真正被处理的那两样东西，
      // 而且要和下面两条按钮用同一个动作词（都是「补齐缺…」），一眼能对上。
      // 要不要花钱挂在按钮上；有活干的时候直接显示还剩多少。
      //
      // 颜色也跟着这个思路走：它是最贵、最不该随手点的按钮，所以平时和「更多筛选」
      // 一样低调，只有 AI 真的在逐题处理时才亮成主色 —— 该看它的时候才显眼。
      const working = queuedTags + queuedAnswers;
      if (working) batch.dataset.working = "true";
      const summary = node("summary", "", "批量补标签和答案");
      summary.append(node("span", "library-batch-cost", "要花额度"));
      summary.append(working
        ? node("span", "library-batch-live", `处理中 ${working} 道`)
        : node("span", "library-batch-count", `${batchActions.length} 项`));
      summary.title = working
        ? `AI 正在逐题处理，还剩 ${working} 道。做完会自动显示，不用一直点着看。`
        : "一次让 AI 处理很多题：补齐缺的知识点标签、生成缺的参考答案。每道题调用一次 AI，会消耗额度。"
          + "点开看具体是哪几项、分别多少道题。";
      batch.append(summary);
      const body = node("div", "library-batch-ai-body");
      body.append(node("p", "helper", apiReady
        ? "这几项都会让 AI 逐题处理、消耗额度。生成的知识点标签只是建议，要你逐条核对确认后才贴到题目上；"
          + "鼠标放在按钮上会说明它处理哪些题。点之前建议先看一眼题数。"
        : "需要先到「设置 → API 配置」里配置并测试通过，才能用这几项。"));
      // 排队中、以及「已经在处理多少道」都写在这里。按钮上方、正对着按钮，
      // 用户盯着按钮时一眼就能看见；只靠一闪而过的提示条必然会被错过。
      const stateLine = node("p", "library-batch-state");
      // 排队中这一行也要占住，而且必须能扛住重画：登记途中界面会重画，
      // 这一行每次都是新节点，所以排队文案要能按需重填，不能抱着旧节点不放。
      stateLine.dataset.tone = "working";
      stateLine.hidden = queueBusy ? false : !working;
      stateLine.textContent = queueBusy ? queueingText(queueCount, 0) : workingSummary(queuedTags, queuedAnswers);
      if (queueBusy) stateLine.dataset.queueing = "true"; else delete stateLine.dataset.queueing;
      body.append(stateLine);
      const actions = node("div", "library-tool-actions");
      actions.append(...batchActions);
      body.append(actions);
      batch.append(body);
      batch.addEventListener("toggle", () => { aiBatchToolsOpen = batch.open; });
      batchBox.append(batch);
    }
  }

  function currentFilterPayload() {
    return { q: state.q.trim(), document: state.document, type: state.type, review: state.review,
      answer: state.answer, tag: state.tag, tag_all: state.tagAll, tag_any: state.tagAny, tag_not: state.tagNot,
      tag_mode: state.tagMode, untagged_knowledge: state.untaggedKnowledge ? "true" : "",
      tag_review: state.tagReview ? "true" : "" };
  }

  function workingSummary(queuedTags, queuedAnswers) {
    const parts = [];
    if (queuedTags) parts.push(`知识点标签 ${queuedTags} 道`);
    if (queuedAnswers) parts.push(`参考答案 ${queuedAnswers} 道`);
    return `AI 正在处理：${parts.join("、")}。它一道一道做，做完自动显示，可以关掉这个面板去做别的。`;
  }

  // 整库排队是逐题登记，题多时后端要忙十几秒。原来这段时间按钮不禁用、
  // 不变样、也没有任何提示，屏幕上一动不动，看着就像卡死。
  // 所以点下去的第一件事就是让按钮本身说「我在动」，并把已等秒数打出来。
  let queueTick = null;
  let queueStartedAt = 0;

  function queueingText(count, spent) {
    const tail = spent ? `，已等 ${spent} 秒` : "";
    return count
      ? `正在登记 ${count} 道题${tail}。软件要一道一道记下来，题越多等得越久，这期间别重复点。`
      : `正在登记${tail}。软件要一道一道记下来，题越多等得越久，这期间别重复点。`;
  }

  function beginQueueFeedback(action, count) {
    queueStartedAt = Date.now();
    // 按钮长什么样交给 applyBatchButtonState：重画多少次都会重新写上，
    // 不用在这里单独照顾「刚才被点的那个节点」—— 它随时会被换掉。
    queueBusy = true;
    queueAction = action || "";
    applyBatchButtonState();
    queueCount = count;
    const tick = () => {
      const spent = Math.round((Date.now() - queueStartedAt) / 1000);
      // 每秒都重新找这一行：登记途中界面会重画，按住开始时那个节点，
      // 等于对着已经不在页面上的元素写字，写了也没人看得见。
      const stateLine = document.querySelector("#aiBatchTools .library-batch-state");
      if (stateLine) {
        stateLine.hidden = false;
        stateLine.dataset.tone = "working";
        stateLine.dataset.queueing = "true";
        stateLine.textContent = queueingText(count, spent);
      }
      setStatus(count ? `正在排队 ${count} 道题…` : "正在排队…", "working");
    };
    tick();
    window.clearInterval(queueTick);
    queueTick = window.setInterval(tick, 1000);
    return () => {
      window.clearInterval(queueTick);
      queueTick = null;
      queueBusy = false;
      queueAction = "";
      queueCount = 0;
      // 还原也走同一条路：现在页面上不管是哪一个按钮节点，都会被改回它自己的名字。
      applyBatchButtonState();
      // 刚才写的是「正在登记…」。登记已经结束了，留着会说谎；
      // 紧接着的刷新会把它换成真实的「AI 正在处理：还有多少道」。
      const stateLine = document.querySelector("#aiBatchTools .library-batch-state");
      if (stateLine && stateLine.dataset.queueing === "true") {
        delete stateLine.dataset.queueing;
        stateLine.hidden = true;
      }
    };
  }

  function confirmBulkTags(count, untaggedTotal) {
    confirmDialog({
      title: `给整个题库的 ${count} 道题生成标签建议？`,
      text: `这 ${count} 道题还没有生成过标签建议，现在一起交给 AI。`
        + (untaggedTotal > count
          ? `题库里一共 ${untaggedTotal} 道题还没有正式知识点标签，其余的已经在等、在生成，或已有建议等你核对，不会重复排队。`
          : "")
        + "每道题调用一次 AI，会消耗额度。生成的标签只是建议，不会直接贴到题目上，要你逐条核对。"
        + "登记时要一道一道写进去，点下去后按钮会立刻变成「正在排队…」，几十秒内不会白等。",
      ok: "开始生成"
    }).then((confirmed) => { if (confirmed) queueJobs("tags", { missing: true }, "tags", count); });
  }

  function confirmBulkAnswers(count) {
    confirmDialog({
      title: `给整个题库里 ${count} 道缺答案的题生成 AI 参考答案？`,
      text: `这 ${count} 道题原卷里没有答案，也没有 AI 参考答案，现在一起交给 AI。`
        + "每道题调用一次 AI，会消耗额度。生成的答案标着「AI 参考 · 未核对」，和原卷答案分开存放。"
        + "登记时要一道一道写进去，点下去后按钮会立刻变成「正在排队…」，几十秒内不会白等。",
      ok: "开始生成"
    }).then((confirmed) => { if (confirmed) queueJobs("answer", { missing: true }, "answer", count); });
  }

  async function queueJobs(kind, target, action, count = 0) {
    const stop = beginQueueFeedback(action, count);
    try {
      const configurationResponse = await fetch("/api/settings/library-ai", { cache: "no-store" });
      const configuration = await configurationResponse.json();
      if (!configurationResponse.ok || configuration.mode !== "api" || configuration.api_ready !== true) throw new Error("请到“设置 → API 配置”配置并测试答题 API 后再生成。");
      const response = await fetch("/api/library/jobs", {
        method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
        body: JSON.stringify({ kind, ...target, api_only: true })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "没能排上队");
      const what = kind === "tags" ? "生成 AI 标签建议" : "做 AI 参考答案";
      const skipped = body.skipped ? `；${body.skipped} 道已有结果或当前不能生成` : "";
      toast(body.queued ? `已排队${what}：${body.queued} 道题，做好会自动显示${skipped}`
        : `没有需要${what}的题${skipped}`, body.queued ? "success" : "");
      setStatus(body.queued ? `已排队 ${body.queued} 道${kind === "tags" ? "题的标签建议" : "题的 AI 参考答案"}，做好后自动显示。` : "没有需要处理的题。", body.queued ? "success" : "");
      stop();
      load({ quiet: true });
    } catch (error) {
      stop();
      toast(`${error.message || "没能排上队"} 请到“设置 → API 配置”检查服务商、密钥和模型。`, "error");
      setStatus("没能排上队。", "error");
    }
  }

  // 只在题库里有 AI 审核入库的题时出现：可以只看人工核对过的，或把 AI 审核的挑出来抽查。
  function renderReviewFilters(reviews, emptied = false) {
    const box = $("reviewFilters");
    const ai = Number(reviews.ai) || 0;
    const human = Number(reviews.human) || 0;
    box.hidden = !emptied && !ai && !state.review;
    box.replaceChildren();
    if (box.hidden) return;
    [["", "全部", ai + human], ["human", "人工核对", human], ["ai", "AI 审核", ai]].forEach(([key, label, count]) => {
      const button = node("button", `draft-filter${state.review === key ? " active" : ""}`);
      button.append(node("span", "", label), node("span", "count", count));
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.review === key));
      if (key === "ai") button.title = "AI 助手审核后入库、还没人工核对的题";
      // 和题型、答案两档一致：当前筛选下一道都没有的那档置灰，但仍看得见。
      if (!count && state.review !== key) {
        button.dataset.emptyFacet = "1";
        button.title = `当前筛选下没有「${label}」的题`;
      }
      button.addEventListener("click", () => { state.review = key; syncUrl(); load(); });
      box.append(button);
    });
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN");
  }

  let questionViewer = null;
  function questionViewerNavigation(item) {
    const selected = state.view === "selected", items = (selected ? visibleItems() : state.items).slice();
    const index = items.findIndex(value => value.id === item.id);
    if (index < 0) return null;
    const query = new URLSearchParams(state.resultQuery || libraryQuery().toString());
    query.delete("limit"); query.delete("offset");
    const view = state.view, generation = state.resultGeneration || 0;
    const basket = selected ? JSON.stringify(state.basket) : "";
    const total = selected ? items.length : Math.max(items.length, Number(state.total) || 0);
    const stillCurrent = () => state.view === view && (state.resultGeneration || 0) === generation
      && (!selected || JSON.stringify(state.basket) === basket);
    const assertCurrent = signal => {
      if (signal?.aborted) throw new Error("读取下一题已停止");
      if (!stillCurrent()) throw new Error("题库结果已更新，请返回题库重新打开后继续看题。");
    };
    return { index, total,
      note: selected && state.basket.length > items.length ? `已选 ${state.basket.length} 题，其中 ${state.basket.length - items.length} 题暂不可查看；按可用题目浏览。` : "",
      async load(position, { signal } = {}) {
        assertCurrent(signal);
        if (!Number.isInteger(position) || position < 0 || position >= total) throw new Error("已到当前结果的边界");
        if (position >= items.length) {
          const next = new URLSearchParams(query); next.set("limit", "40"); next.set("offset", String(items.length));
          const response = await fetch(`/api/library?${next}`, { cache: "no-store", signal });
          const body = await response.json();
          assertCurrent(signal);
          if (!response.ok) throw new Error(body.error || "下一页题目读取失败，请重试。");
          if (!Array.isArray(body.items) || !body.items.length || !Number.isInteger(body.total) || body.total !== total
              || items.length + body.items.length > total
              || body.items.some(value => !value?.id || items.some(known => known.id === value.id))
              || new Set(body.items.map(value => value.id)).size !== body.items.length) {
            throw new Error("题库结果已有变化或返回不完整，请返回题库刷新后继续。");
          }
          items.push(...body.items);
        }
        assertCurrent(signal);
        if (!items[position]) throw new Error("下一题暂未返回，请重试。");
        return { item: items[position], index: position, total };
      }
    };
  }
  function openQuestionViewer(item, returnFocus) {
    if (!questionViewer) questionViewer = window.LibraryQuestionViewer.create({ node, QB, solutions });
    return questionViewer.open(item, { returnFocus, navigation: questionViewerNavigation(item),
      returnFocusResolver: () => document.getElementById(`q-${item.id}`)?.querySelector(".library-full-button") });
  }
  // 收侧栏的小按钮就长在侧栏右边那道缝上（.rail-collapse），抽屉里不再放一份。
  const focusToggle = window.LibraryQuestionViewer.mountFocus({ button: $("libraryRailToggle") });

  // 题卡的「更多」是原生 <details>：点开以后，点页面别处 —— 包括一大片空白 ——
  // 它不会自己收起来，只能再点一次「更多」。这里补上：点外面关、Esc 关、
  // 开一个就把别的收起来。三条规则加在一起八行，比重写成一个自绘菜单划算得多。
  function closeOtherCardMenus(except) {
    for (const node of document.querySelectorAll(".library-card-more[open]")) if (node !== except) node.open = false;
  }
  document.addEventListener("pointerdown", (event) => {
    const open = document.querySelector(".library-card-more[open]");
    if (open && !open.contains(event.target)) open.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = document.querySelector(".library-card-more[open]");
    if (!open) return;
    // 顺序要紧：先把焦点挪到「更多」上，再收菜单。反过来的话，刚才被 Tab 进去的那个
    // 按钮会在收起来之后变成隐藏元素，浏览器要等到布局处理完才把焦点甩回 body ——
    // 那一步在我们 focus() 之后才发生，键盘用户就整个找不到位置了。
    const summary = open.querySelector("summary");
    summary?.focus({ preventScroll: true });
    open.open = false;
    // 还有一层：题库在后台刷新（题卡重画、筛选下拉重新灌数据）会在几十毫秒到一秒多
    // 之后把焦点整个甩回 body，而且没人调 blur()、元素也好好地在那儿 —— 浏览器只是
    // 把「焦点在哪儿」重置了。那一下用户看不见，键盘用户感觉得到：按完 Esc 一看，
    // 焦点跑到页首去了。这里守着补上：焦点不在 summary 又不在 body（用户自己点到别处
    // 了）就立刻收手，最多守两秒。
    let until = performance.now() + 2000;
    const hold = () => {
      if (!summary?.isConnected || performance.now() > until) return;
      const active = document.activeElement;
      if (active !== document.body && active !== summary) return;
      if (active === document.body) summary.focus({ preventScroll: true });
      setTimeout(hold, 50);
    };
    setTimeout(hold, 50);
  });

  // 录过两次的卷在题库里同名。日期只到天仍会撞（同一天录两次照样分不出），
  // 所以带上时分 —— 用户要判断的正是「哪份是后录的」。
  function shortDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间未知"
      : date.toLocaleString("zh-CN", { hour12: false, year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit" });
  }

  // 来源下拉、题卡和撤回确认框必须显示同一个时间，否则用户对着看会以为
  // 是两条不同记录。统一从 facets 里取该来源的首次入库时间。
  function sourceFirstSeen(documentId, fallback) {
    const row = (state.facets?.sources || []).find((source) => source.document_id === documentId);
    return row?.first_published_at || fallback;
  }

  function fullDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { hour12: false });
  }

  // 录过两次的卷在题库里同名，撤回一批前必须能分清哪份是哪份。
  function duplicateSourceNames() {
    const names = new Map();
    for (const item of state.catalog.values()) names.set(item.source_filename, (names.get(item.source_filename) || 0) + 1);
    return new Set([...names].filter(([, count]) => count > 1).map(([name]) => name));
  }

  // 卡上那句「原卷第 N 题」是老师回原卷核对时要用的定位。
  // 试卷的题号就是原卷先后，光它就够；教材每一章的习题都从 1 重新编，
  // 九年级上册里就有 94 个「第 1 题」，光这句话翻书根本找不到是哪一章。
  // 章节名（习题 6.1、复习题）后端已经算好在 content.section 里，只有短标签
  // 才显示——试卷的 section 是一整段大题说明（「一、选择题：本大题共8小题…」），
  // 放上去反而是噪音。
  function shortSection(item) {
    const section = String(item?.content?.section || "").trim();
    if (!section || section.length > 12 || /[：:，,。;；]/.test(section)) return "";
    return section;
  }

  // 教材「做一做」「随堂练习」里的题纸面上没有印刷题号，后端给 null。
  // 翻书时用得上的定位是栏目标题本身；印一个「第 0 题」只会让人翻不到。
  function sourceLocator(item) {
    if (item?.number) return `第 ${item.number} 题`;
    return shortSection(item) || "栏目题";
  }

  function cardLocator(item) {
    const section = shortSection(item);
    if (!item?.number) return section || "栏目题";
    const number = `原卷第 ${item.number} 题`;
    return section ? `${section} · ${number}` : number;
  }

  function card(item) {
    const article = node("article", `library-card${state.basket.includes(item.id) ? " in-basket" : ""}${state.inspectorId === item.id ? " inspector-active" : ""}`);
    article.id = `q-${item.id}`;
    article.tabIndex = 0;
    article.setAttribute("aria-label", `${sourceLocator(item)}，点击查看题目详情`);
    if (state.inspectorId === item.id) article.setAttribute("aria-current", "true");
    article.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, select, textarea, summary, details, label, [contenteditable='true']")) return;
      if (window.getSelection()?.toString()) return;
      selectInspectorQuestion(item, article);
    });
    article.addEventListener("keydown", (event) => {
      if (event.isComposing || event.keyCode === 229 || event.target !== article || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "Enter") { event.preventDefault(); selectInspectorQuestion(item, article); setInspectorOpen(true); }
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        const cards = Array.from(document.querySelectorAll(".library-card")), index = cards.indexOf(article);
        const next = cards[index + (event.key === "ArrowDown" ? 1 : -1)];
        if (next) { event.preventDefault(); next.focus(); const target = state.catalog.get(next.id.slice(2)); if (target) selectInspectorQuestion(target, next); }
      }
    });
    const meta = node("header", "library-card-meta");
    const select = node("input", "library-card-select");
    select.type = "checkbox";
    select.checked = state.selected.has(item.id);
    select.setAttribute("aria-label", `勾选${item.source_filename}${sourceLocator(item)}`);
    select.addEventListener("change", () => {
      if (select.checked) state.selected.add(item.id); else state.selected.delete(item.id);
      article.classList.toggle("is-selected", select.checked);
      syncSelection();
    });
    // 题号不能直接拼：教材「做一做」「随堂练习」里的题纸面上没有印刷题号，
    // 后端给 null/0，硬拼会印出「第 0 题」——翻书翻不到的一句话比没有更糟。
    // sourceLocator 负责兜底（无题号就给栏目标题）。
    const heading = node("div", "library-card-heading");
    heading.append(node("h3", "library-number", sourceLocator(item)), typeChip(item));
    const sourceName = node("span", "library-source", item.source_filename);
    sourceName.title = item.source_filename || "试卷";
    const provenance = node("div", "library-card-provenance");
    meta.append(select, heading, sourceName, provenance);
    // 定位句只在它真能补上信息时才显示：
    //   试卷卡——标题已经写着「第 N 题」，再补一句「原卷第 N 题」是同一句话说两遍；
    //   教材卡——每一章的习题都从 1 重新编，全库有 94 张都写着「第 1 题」，
    //             只有带上章节（习题 6.1 · 原卷第 1 题）才认得出是哪一章。
    // 放在 provenance 里而不是卡头：卡头是三列 grid（18px 复选框 | 标题 | 文件名），
    // 一句没有类名的定位会被自动排进那个 18px 的窄槽，每个字换一行，竖成一列。
    if (shortSection(item)) provenance.append(node("span", "", cardLocator(item)));
    // 1.12.6：同一份卷被录过两次时，两份来源在题库里同名，只看文件名分不出
    // 哪份是哪份。补上录入时间，撤回一批时才不会撤错那一份。
    if (duplicateSourceNames().has(item.source_filename)) {
      const when = node("span", "library-note quiet", `录于 ${shortDate(sourceFirstSeen(item.document_id, item.published_at))}`);
      when.title = `这份来源的入库时间：${fullDate(sourceFirstSeen(item.document_id, item.published_at))}`;
      provenance.append(when);
    }
    if (item.content?.body_mode === "source_image") provenance.append(node("span", "library-note", "原图题"));
    if (item.origin) {
      const origin = node("span", "library-origin", `题源：${item.origin}`);
      origin.title = "题源：题干前印的出处。组卷打印时默认不印";
      provenance.append(origin);
    }
    if (state.features.subquestions && item.subquestions >= 2) provenance.append(node("span", "library-note", `含 ${item.subquestions} 小问`));
    if (solutions.hasContent(item.solution)) {
      provenance.append(node("span", "library-note quiet", "已保存答案解析"));
    } else if (!item.has_answer) {
      const missing = node("span", "library-note quiet", item.ai_answer && state.features.ai_answer ? "原卷无答案 · 有 AI 参考" : "无答案");
      missing.title = "原卷没有给答案";
      provenance.append(missing);
    }
    if (item.review?.source === "ai") {
      const badge = node("span", "library-review ai", `${item.review.agent || "AI"} 审核`);
      badge.title = "这道题是 AI 助手对照原卷后审核入库的，还没人工核对。回到题卡确认后会变成人工核对。";
      provenance.append(badge);
    }
    const paper = node("div", "paper");
    const summary = workspace.summaryStem(item.content?.stem, QB);
    const expanded = state.expanded.has(item.id);
    if (expanded) QB.renderQuestion(paper, item.content, { showNumber: false, showAnswer: "none" });
    else QB.renderQuestion(paper, { ...item.content, stem: summary.text, options: {}, figures: (item.content?.figures || []).filter((figure) => figure.slot === "stem").slice(0, 2),
      question_images: (item.content?.question_images || []).slice(0, 1) }, { showNumber: false, showAnswer: "none" });
    article.classList.toggle("compact", !expanded);
    article.classList.toggle("is-selected", state.selected.has(item.id));
    if (!expanded) {
      const notes = [];
      if (item.content?.body_mode === "source_image") notes.push(`原卷截图 ${(item.content.question_images || []).length} 段`);
      if (summary.folded) notes.push("还有题干与小问");
      if (Object.keys(item.content?.options || {}).length) notes.push("含选项");
      if ((item.content?.figures || []).length > 2) notes.push(`含 ${(item.content.figures || []).length} 张配图`);
      // The repeated "点全屏看题查看" nudge can be permanently switched off.
      // The facts about what was folded stay, and the button below the card is
      // always there, so the complete question is never harder to find.
      if (notes.length) {
        const hintOff = hintHidden("preview-note");
        paper.append(node("p", "library-preview-note",
          notes.join(" · ") + (hintOff ? "" : " · 预览")));
      }
    }
    const reveal = answerReveal(item);
    if (reveal) paper.append(reveal);
    const body = node("div", "library-card-body");
    body.append(paper);
    const actions = node("footer", "library-card-actions");
    const origin = iconButton("button", "button button-quiet button-small", "查看出处", "source");
    origin.addEventListener("click", () => { selectInspectorQuestion(item, article); openSource(item); });
    const full = node("button", "button button-small library-full-button", "全屏看题");
    full.type = "button";
    full.addEventListener("click", () => { selectInspectorQuestion(item, article); openQuestionViewer(item, full); });
    const expand = node("button", "button button-quiet button-small", expanded ? "收起题面" : "展开题面");
    expand.type = "button";
    expand.setAttribute("aria-expanded", String(expanded));
    expand.addEventListener("click", () => {
      selectInspectorQuestion(item, article);
      if (expanded) state.expanded.delete(item.id); else state.expanded.add(item.id);
      const replacement = card(item);
      article.replaceWith(replacement);
      replacement.querySelector('[aria-expanded]')?.focus({ preventScroll: true });
    });
    const more = node("details", "library-card-more");
    more.append(node("summary", "", "更多"));
    const menu = node("div", "library-card-menu");
    const closeMenu = () => { more.open = false; };
    const review = iconButton("a", "button button-quiet button-small", "回到题卡", "back");
    review.href = draftLink(item);
    review.addEventListener("click", closeMenu);
    const withdraw = iconButton("button", "button button-quiet button-small library-withdraw", "撤回", "undo");
    withdraw.title = "从正式题库撤下；题卡和原卷都保留，可以重新入库";
    withdraw.addEventListener("click", () => { closeMenu(); withdrawItem(item); });
    const inBasket = state.basket.includes(item.id);
    const basket = iconButton("button", `button ${inBasket ? "button-outline" : ""} button-small`, inBasket ? "已在试题篮" : "加入试题篮", inBasket ? "check" : "plus");
    basket.setAttribute("aria-pressed", String(inBasket));
    basket.addEventListener("click", () => {
      if (!inBasket && state.basket.length >= 500) { toast("试题篮最多放 500 题，请先保存一份组卷", "error"); return; }
      state.basket = inBasket ? state.basket.filter((id) => id !== item.id) : state.basket.concat(item.id);
      saveBasket();
      render();
    });
    const editAnswer = iconButton("button", "button button-quiet button-small", "编辑答案解析", "plus");
    editAnswer.addEventListener("click", () => { selectInspectorQuestion(item, article); closeMenu(); openAnswerEditor([item], { scope: "library" }); });
    const editQuestion = node("button", "button button-quiet button-small", "修改题目");
    editQuestion.type = "button"; editQuestion.addEventListener("click", () => { selectInspectorQuestion(item, article); closeMenu(); openQuestionEditor(item); });
    menu.append(editQuestion, editAnswer, review, withdraw, ...jobButtons(item));
    more.append(menu);
    // 原生 details 各开各的。开一个就把别的收起来，页面上不会同时挂好几个小窗口。
    more.addEventListener("toggle", () => { if (more.open) closeOtherCardMenus(more); });
    const detailsButton = node("button", "button button-small library-card-details", "标签与详情"); detailsButton.type = "button";
    detailsButton.addEventListener("click", () => { selectInspectorQuestion(item, article); setInspectorOpen(true); });
    actions.append(origin, full, expand, detailsButton, more, node("span", "actions-spacer"), basket);
    const context = cardTagContext(item);
    if (context.childNodes.length) body.append(context);
    article.append(meta, body, actions);
    if (state.focus === item.id) article.classList.add("focused");
    return article;
  }

  function selectInspectorQuestion(item, article = null) {
    const previous = state.inspectorId;
    if (previous && ui.inspectorContent) inspectorScrolls.set(previous, ui.inspectorContent.scrollTop);
    state.inspectorId = item?.id || "";
    if (previous && previous !== state.inspectorId) {
      const oldCard = document.getElementById(`q-${previous}`);
      oldCard?.classList.remove("inspector-active");
      oldCard?.removeAttribute("aria-current");
    }
    const target = article || document.getElementById(`q-${state.inspectorId}`);
    target?.classList.add("inspector-active");
    target?.setAttribute("aria-current", "true");
    renderInspector(item);
    if (!window.matchMedia("(max-width: 979px)").matches) setInspectorOpen(true);
    else ui.inspectorToggle?.setAttribute("aria-expanded", String(document.body.classList.contains("library-inspector-open")));
  }

  function locateInspectorQuestion(item) {
    const target = document.getElementById(`q-${item.id}`);
    if (!target) { toast("这道题当前不在列表中，请先清除筛选。", "error"); return; }
    if (window.matchMedia("(max-width: 979px)").matches) setInspectorOpen(false);
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.remove("inspector-focus-pulse");
    void target.offsetWidth;
    target.classList.add("inspector-focus-pulse");
    window.setTimeout(() => target.classList.remove("inspector-focus-pulse"), 1900);
  }

  function captureLibraryViewport() {
    const scroller = document.scrollingElement || document.documentElement;
    const inView = element => {
      const rect = element?.getBoundingClientRect(); return rect && rect.bottom > 0 && rect.top < window.innerHeight;
    };
    // Hover correction can belong to a different card than the inspector. An
    // offscreen inspector card must never pull the reader back to that card.
    const hovered = document.getElementById(`q-${tagHover?.publicationId || ""}`), inspected = document.getElementById(`q-${state.inspectorId}`);
    const anchor = inView(hovered) ? hovered : inView(inspected) ? inspected
      : Array.from(ui.list.querySelectorAll(".library-card")).find(inView);
    return { scrollTop: scroller.scrollTop, scrollLeft: scroller.scrollLeft,
      anchorId: anchor?.id || "", anchorTop: anchor?.getBoundingClientRect().top };
  }

  function restoreLibraryViewport(position) {
    if (!position) return;
    requestAnimationFrame(() => {
      const scroller = document.scrollingElement || document.documentElement;
      const anchor = position.anchorId ? document.getElementById(position.anchorId) : null;
      if (anchor && Number.isFinite(position.anchorTop)) {
        scroller.scrollTop += anchor.getBoundingClientRect().top - position.anchorTop;
      } else {
        scroller.scrollTop = position.scrollTop;
        scroller.scrollLeft = position.scrollLeft;
      }
    });
  }

  function inspectorFact(facts, label, value) {
    if (value === undefined || value === null || value === "") return;
    const row = node("div", "question-inspector-fact");
    row.append(node("dt", "", label), node("dd", "", value));
    facts.append(row);
  }

  // 从标签窗口跳回右侧面板核对：把面板切到这道题，并滚到它的建议清单上。
  // 关掉窗口就回到主界面，所以这里要确保面板真的开着（窄屏上它默认收起），
  // 并且强制重画一次 —— renderInspector 见到同一道题会直接跳过，滚不到刚渲染的块。
  function focusInspectorTag(item) {
    if (window.matchMedia("(max-width: 979px)").matches) setInspectorOpen(true);
    inspectorRenderedKey = "";
    renderInspector(item);
    const card = ui.inspectorContent && ui.inspectorContent.querySelector(".question-inspector-tags .tag-suggestion-card");
    if (card) card.scrollIntoView({ block: "nearest" });
  }

  function renderInspector(item) {
    if (!ui.inspectorContent) return;
    const renderKey = item ? JSON.stringify([item, state.features]) : "empty";
    if (renderKey === inspectorRenderedKey) return;
    inspectorRenderedKey = renderKey;
    const content = ui.inspectorContent;
    if (item && content.dataset.questionId === item.id) inspectorScrolls.set(item.id, content.scrollTop);
    content.dataset.questionId = item?.id || "";
    content.replaceChildren();
    if (!item) {
      const empty = node("div", "question-inspector-empty");
      empty.append(node("strong", "", "选择一道题"), node("p", "helper", "点击中间的题卡，这里会显示题目来源、标签和核对信息。"));
      content.append(empty);
      if (ui.inspectorToggle) ui.inspectorToggle.disabled = true;
      return;
    }
    if (ui.inspectorToggle) ui.inspectorToggle.disabled = false;
    const heading = node("div", "question-inspector-title");
    heading.append(node("h3", "", sourceLocator(item)), node("span", "library-type", QB.TYPE_NAMES[item.question_type] || item.question_type || "题目"));
    content.append(heading, node("p", "question-inspector-source", item.source_filename || "试卷"));
    const targetHint = node("div", "question-inspector-target");
    const currentLabel = node("span", "", "当前选中");
    currentLabel.title = "右侧信息对应绿色高亮题卡";
    targetHint.append(currentLabel);
    const locate = node("button", "button button-small question-inspector-locate", "定位题卡");
    locate.type = "button"; locate.addEventListener("click", () => locateInspectorQuestion(item));
    targetHint.append(locate); content.append(targetHint);

    const extras = extrasNode(item);
    if (extras) {
      const section = node("section", "question-inspector-tags");
      const sectionTitle = node("div", "question-inspector-section-title");
      sectionTitle.append(node("h4", "", "标签"));
      const history = extras.querySelector(".library-tag-history-link");
      if (history) sectionTitle.append(history);
      section.append(sectionTitle, extras);
      content.append(section);
    }

    const info = node("details", "question-inspector-info");
    info.append(node("summary", "", "题目信息"));
    const facts = node("dl", "question-inspector-facts");
    inspectorFact(facts, "来源", item.source_filename || "试卷");
    inspectorFact(facts, "核对", item.review?.source === "ai" ? `${item.review.agent || "AI"} 审核 · 待人工核对`
      : item.review?.source === "human" ? "已人工核对" : "已入正式题库");
    inspectorFact(facts, "答案解析", solutions.hasContent(item.solution) ? "已保存解析"
      : item.has_answer ? "原卷提供答案" : item.ai_answer && state.features.ai_answer ? "有 AI 参考答案 · 未核对" : "暂无答案解析");
    if (item.origin) inspectorFact(facts, "题源", item.origin);
    if (state.features.subquestions && item.subquestions >= 2) inspectorFact(facts, "小问", `${item.subquestions} 问`);
    info.append(facts);

    const preview = node("div", "paper question-inspector-paper");
    QB.renderQuestion(preview, item.content || {}, { showNumber: false, showAnswer: "none" });
    const previewDetails = node("details", "question-inspector-preview");
    previewDetails.append(node("summary", "", "查看当前题面"), preview);
    content.append(previewDetails, info);
    content.scrollTop = inspectorScrolls.get(item.id) || 0;
    ui.inspectorToggle?.setAttribute("aria-expanded", String(window.matchMedia("(max-width: 979px)").matches
      ? document.body.classList.contains("library-inspector-open") : !document.body.classList.contains("library-inspector-collapsed")));
  }

  function setInspectorOpen(open) {
    const next = Boolean(open);
    const mobile = window.matchMedia("(max-width: 979px)").matches;
    if (next) {
      if (document.body.classList.contains("rail-open")) setRail(false);
      if (document.body.classList.contains("library-basket-open")) setBasketPanel(false);
    }
    document.body.classList.toggle("library-inspector-open", mobile && next);
    if (!mobile) document.body.classList.toggle("library-inspector-collapsed", !next);
    const scrim = $("inspectorScrim"); if (scrim) scrim.hidden = !mobile || !next;
    ui.inspectorToggle?.setAttribute("aria-expanded", String(next));
  }

  function draftLink(item, extra = "") {
    return item.document_id
      ? `/?document=${encodeURIComponent(item.document_id)}${item.draft_id ? `&draft=${encodeURIComponent(item.draft_id)}` : ""}${extra}`
      : "/";
  }

  // 题型没定的题（1.10 以前入库的）：点一下回到题卡选题型，再通过、入库就是新的一版。
  function typeChip(item) {
    const label = QB.TYPE_NAMES[item.question_type] || item.question_type;
    if (item.question_type !== "unknown" || !item.document_id) return node("span", "library-type", label);
    const link = node("a", "library-type undecided", "题型待核对 · 去选题型");
    link.href = draftLink(item, "&fix=type");
    link.title = "回到题卡，在题号旁边选题型；再标记通过、入库，题库里就是新的一版";
    return link;
  }

  // 每道题单独“看答案”：几百道题不会一下全摊开（1.10.5）。原卷的答案与解析、
  // AI 参考答案（打开了这个功能时）都在里面；没有答案的题不出这个按钮。
  function answerReveal(item) {
    const content = item.content || {};
    const original = Boolean(String(content.answer ?? "").trim() || String(content.analysis ?? "").trim());
    const ai = item.ai_answer && state.features.ai_answer ? item.ai_answer : null;
    const saved = item.solution && solutions.hasContent(item.solution) ? item.solution : null;
    if (!original && !ai && !saved) return null;
    const box = node("details", "library-answer");
    box.open = state.opened.has(item.id);
    const summary = node("summary", "library-answer-toggle");
    summary.append(node("span", "when-closed", "看答案"), node("span", "when-open", "收起答案"));
    box.append(summary);
    box.addEventListener("toggle", () => {
      if (box.open) state.opened.add(item.id); else state.opened.delete(item.id);
    });
    const history = saved && (original || ai) ? node("details", "library-answer-history") : null;
    if (history) history.append(node("summary", "", ai ? "原卷答案与 AI 初稿历史" : "原卷答案历史"));
    if (original) {
      const part = node("div", "qb-answer");
      part.append(...QB.answerRows(document, content));
      (history || box).append(part);
    }
    if (saved) {
      const part = node("div", "qb-answer");
      part.append(node("p", "library-answer-label", item.solution_needs_review ? "保存的答案解析 · 题面有改动，待核对" : "保存的答案解析"));
      const body = node("div"); solutions.render(body, saved, { node, QB, subQuestions: true }); part.append(body); box.append(part);
    }
    if (ai) {
      const part = node("div", "qb-answer ai-answer");
      const label = node("p", "library-answer-label", "AI 参考答案 · 未核对");
      label.title = `${ai.engine || "解题模型"} 生成的初稿，尚未确认数学内容；保存后的答案优先显示，初稿保留作历史对照。`;
      const rows = QB.answerRows(document, ai, { empty: "（空）" });
      if (!String(ai.analysis || "").trim()) rows.pop();
      part.append(label, ...rows);
      (history || box).append(part);
    }
    if (history) box.append(history);
    return box;
  }

  // Stable tag identities are managed separately from immutable question text.
  let tagDialog = null, tagManagerDialog = null, tagCatalogueEditorDialog = null, tagTemplateDialog = null, tagCatalogue = { revision: 1, tags: [] };
  let inspectorRenderedKey = null;
  const inspectorScrolls = new Map(), inlineTagStates = new Map(), inlineTagLocks = new Set(), suggestionLocks = new Set(), suggestionAttempts = new Map();
  let tagHover = null, tagHoverPanel = null, tagHoverOpenTimer = null, tagHoverCloseTimer = null, tagHoverSequence = 0, tagHoverSuppressFocus = false;
  const suggestionDraftKey = "qb-library-tag-suggestion-drafts";
  let suggestionDrafts = {};
  try { suggestionDrafts = JSON.parse(sessionStorage.getItem(suggestionDraftKey) || "{}"); } catch (_) {}
  try { Object.entries(JSON.parse(sessionStorage.getItem(suggestionDraftKey + ":requests") || "{}")).forEach(([id, attempt]) => suggestionAttempts.set(id, attempt)); } catch (_) {}
  function suggestionDraft(item, suggestion) { return suggestionDrafts[item.id + ":" + suggestion.id] ||= {}; }
  function saveSuggestionDrafts() { try { sessionStorage.setItem(suggestionDraftKey, JSON.stringify(suggestionDrafts)); sessionStorage.setItem(suggestionDraftKey + ":requests", JSON.stringify(Object.fromEntries(suggestionAttempts))); } catch (_) {} }
  let tagManagerQuery = "";
  let tagTargetIds = [], tagRevisions = {}, tagMemberships = {}, tagActiveSection = "all", tagPickerView = "all", tagUndoId = "", tagOperationHistory = [];
  const catalogueAttempts = new Map();
  let catalogueUnknown = null;
  let pickerAttempt = null;
  const tagRequestId = () => crypto.randomUUID ? crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0; return (c === "x" ? r : (r & 3 | 8)).toString(16);
  });
  async function tagRequest(url, options) {
    options = options || {};
    const response = await fetch(url, { cache: "no-store", ...options, headers: {
      "X-QB-Request": "1", ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {})
    } });
    const body = await response.json(); if (!response.ok) {
      const error = new Error(body.error || "标签操作没有完成");
      error.status = response.status; error.stale = body.stale === true; throw error;
    }
    return body;
  }
  async function loadTagCatalogue() { tagCatalogue = await tagRequest("/api/library/tags"); return tagCatalogue; }
  function hierarchyTag(id) { return (tagCatalogue.tags || []).find(tag => tag.id === id); }
  function tagHierarchyRows(id, direction) {
    const tag = hierarchyTag(id);
    const ids = tag?.[direction === "up" ? "parent_ids" : "child_ids"] || [];
    return ids.map(hierarchyTag).filter(Boolean).sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  }
  function tagAncestorPaths(id, limit = 6, rows = tagCatalogue.tags || []) {
    const byId = new Map(rows.map(tag => [tag.id, tag]));
    const paths = [], stack = [{ id, path: [] }];
    while (stack.length && paths.length < limit) {
      const entry = stack.pop(), tag = byId.get(entry.id);
      if (!tag || entry.path.some(value => value.id === tag.id)) continue;
      const path = [tag, ...entry.path], parents = (tag.parent_ids || []).map(parent => byId.get(parent)).filter(Boolean).sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
      if (!parents.length) paths.push(path);
      else parents.slice().reverse().forEach(parent => stack.push({ id: parent.id, path }));
    }
    return { paths, hasMore: stack.length > 0 };
  }
  function cancelTagHoverClose() { window.clearTimeout(tagHoverCloseTimer); }
  function scheduleTagHoverClose() {
    cancelTagHoverClose();
    if (tagHover?.pinned || inlineTagLocks.has(tagHover?.publicationId)) return;
    tagHoverCloseTimer = window.setTimeout(() => {
      if (tagHoverPanel?.matches(":hover") || tagHoverPanel?.contains(document.activeElement) || tagHover?.anchor?.matches(":hover") || document.activeElement === tagHover?.anchor) return;
      closeTagHover();
    }, 320);
  }
  function closeTagHover({ restoreFocus = false } = {}) {
    window.clearTimeout(tagHoverOpenTimer); cancelTagHoverClose(); ++tagHoverSequence;
    const anchor = tagHover?.anchor; tagHover = null;
    if (tagHoverPanel) {
      try { if (tagHoverPanel.matches(":popover-open")) tagHoverPanel.hidePopover(); } catch (_) {}
      tagHoverPanel.hidden = true;
    }
    document.querySelectorAll('[data-tag-hover][aria-expanded="true"]').forEach(chip => chip.setAttribute("aria-expanded", "false"));
    if (restoreFocus && anchor?.isConnected) {
      tagHoverSuppressFocus = true; anchor.focus({ preventScroll: true }); queueMicrotask(() => { tagHoverSuppressFocus = false; });
    }
  }
  function ensureTagHoverPanel() {
    if (tagHoverPanel) return tagHoverPanel;
    const panel = node("div", "library-tag-hover"); panel.id = "libraryTagHover"; panel.hidden = true;
    panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "false"); panel.setAttribute("aria-label", "浏览层级并校正当前题标签");
    if (typeof panel.showPopover === "function") panel.setAttribute("popover", "manual");
    panel.addEventListener("pointerenter", cancelTagHoverClose); panel.addEventListener("pointerleave", scheduleTagHoverClose);
    panel.addEventListener("focusin", cancelTagHoverClose); panel.addEventListener("focusout", scheduleTagHoverClose);
    document.body.append(panel); tagHoverPanel = panel; return panel;
  }
  function positionTagHover() {
    if (!tagHover || !tagHoverPanel || tagHoverPanel.hidden) return;
    let anchor = tagHover.anchor;
    if (!anchor?.isConnected) {
      anchor = Array.from(document.querySelectorAll("[data-tag-hover]")).find(chip => chip.dataset.publicationId === tagHover.publicationId && chip.dataset.tagId === tagHover.sourceTagId);
      if (anchor) tagHover.anchor = anchor;
    }
    const rect = anchor?.getBoundingClientRect() || tagHover.rect;
    if (!rect) return;
    const width = Math.min(400, window.innerWidth - 24), height = Math.min(tagHoverPanel.offsetHeight || 400, window.innerHeight - 24);
    let left = rect.left, top = rect.bottom + 8;
    if (left + width > window.innerWidth - 12) left = rect.right - width;
    if (top + height > window.innerHeight - 12) top = Math.max(12, rect.top - height - 8);
    tagHoverPanel.style.width = `${width}px`;
    tagHoverPanel.style.left = `${Math.max(12, Math.min(left, window.innerWidth - width - 12))}px`;
    tagHoverPanel.style.top = `${Math.max(12, Math.min(top, window.innerHeight - height - 12))}px`;
  }
  function tagHoverChip(item, link, className) {
    const tag = link.tag, chip = node("button", className, tag.name + (tag.kind === "method" && link.scope?.subquestions ? " · 第" + link.scope.subquestions.join("、") + "问" : ""));
    chip.type = "button"; chip.dataset.tagHover = "true"; chip.dataset.publicationId = item.id; chip.dataset.tagId = tag.id;
    chip.setAttribute("aria-haspopup", "dialog"); chip.setAttribute("aria-controls", "libraryTagHover"); chip.setAttribute("aria-expanded", "false");
    chip.title = "浏览上级、下级；点击名称校正当前题标签";
    chip.addEventListener("pointerenter", event => {
      if (event.pointerType === "touch") return;
      cancelTagHoverClose(); window.clearTimeout(tagHoverOpenTimer);
      tagHoverOpenTimer = window.setTimeout(() => openTagHover(item.id, link, chip), 220);
    });
    chip.addEventListener("pointerleave", () => { window.clearTimeout(tagHoverOpenTimer); scheduleTagHoverClose(); });
    chip.addEventListener("focus", () => { if (tagHoverSuppressFocus) return; cancelTagHoverClose(); void openTagHover(item.id, link, chip); });
    chip.addEventListener("blur", scheduleTagHoverClose);
    chip.addEventListener("click", event => { event.stopPropagation(); window.clearTimeout(tagHoverOpenTimer); void openTagHover(item.id, link, chip, true); });
    return chip;
  }
  async function openTagHover(publicationId, link, anchor, pinned = false) {
    cancelTagHoverClose();
    if (tagHover?.publicationId === publicationId && tagHover.sourceTagId === link.tag.id) {
      tagHover.anchor = anchor; tagHover.pinned ||= pinned; anchor.setAttribute("aria-expanded", "true"); positionTagHover(); return;
    }
    if (tagHover && inlineTagLocks.has(tagHover.publicationId)) return;
    closeTagHover();
    const panel = ensureTagHoverPanel(), sequence = ++tagHoverSequence;
    tagHover = { publicationId, sourceTagId: link.tag.id, viewTagId: link.tag.id, link, anchor, rect: anchor.getBoundingClientRect(), pinned, loading: true, up: new Set(), down: new Set() };
    anchor.setAttribute("aria-expanded", "true"); panel.hidden = false;
    renderTagHover();
    try { if (typeof panel.showPopover === "function" && !panel.matches(":popover-open")) panel.showPopover(); } catch (_) {}
    positionTagHover();
    try {
      const relations = await tagRequest("/api/library/tags/" + encodeURIComponent(link.tag.id) + "/relations");
      if (tagCatalogue.revision !== relations.revision || !hierarchyTag(link.tag.id)) await loadTagCatalogue();
      if (sequence !== tagHoverSequence || !tagHover) return;
      tagHover.relations = relations; tagHover.loading = false; renderTagHover();
    } catch (error) {
      if (sequence !== tagHoverSequence || !tagHover) return;
      tagHover.loading = false; tagHover.error = error.message; renderTagHover();
    }
  }
  function renderTagHover() {
    if (!tagHover || !tagHoverPanel) return;
    const hover = tagHover, item = state.catalog.get(hover.publicationId), current = hierarchyTag(hover.sourceTagId) || hover.link.tag;
    const link = item?.tag_assignments?.find(value => value.tag.id === hover.sourceTagId) || hover.link;
    const busy = inlineTagLocks.has(hover.publicationId), attempt = inlineTagStates.get(hover.publicationId)?.attempt;
    const scrollTop = tagHoverPanel.scrollTop; tagHoverPanel.replaceChildren();
    const head = node("header", "library-tag-hover-head"), close = node("button", "library-tag-hover-close", "×");
    close.type = "button"; close.setAttribute("aria-label", "关闭标签层级"); close.addEventListener("click", () => closeTagHover({ restoreFocus: true }));
    head.append(node("strong", "", `第 ${item?.number || ""} 题 · ${current.name}`), close); tagHoverPanel.append(head);
    if (item?.source_filename) tagHoverPanel.append(node("p", "library-tag-hover-source", item.source_filename));
    tagHoverPanel.append(node("small", "library-tag-hover-scope", (current.kind === "method" ? "解题方法" : current.kind === "knowledge" ? "知识点" : "自定义标签")
      + (link.scope?.subquestions ? ` · 第 ${link.scope.subquestions.join("、")} 问` : " · 整题")));
    tagHoverPanel.append(node("p", "library-tag-hover-help", "箭头展开浏览；点击名称替换本题这一条标签。"));
    if (hover.loading) { tagHoverPanel.append(node("p", "helper", "正在读取标签层级…")); positionTagHover(); return; }
    if (hover.error) {
      tagHoverPanel.append(node("p", "library-tag-hover-status", hover.error));
      const retry = node("button", "button button-small", "重试读取"); retry.type = "button";
      retry.addEventListener("click", () => { const saved = { ...hover }; closeTagHover(); void openTagHover(saved.publicationId, saved.link, saved.anchor, saved.pinned); }); tagHoverPanel.append(retry); return;
    }
    const paths = node("div", "library-tag-hover-paths"), ancestors = tagAncestorPaths(current.id);
    for (const path of ancestors.paths) {
      const row = node("div", "library-tag-hover-path");
      path.forEach((tag, index) => {
        if (index) row.append(node("span", "", "›"));
        const button = node("button", "library-tag-hover-name" + (tag.id === current.id ? " current" : ""), tag.name);
        button.type = "button"; button.disabled = busy || Boolean(attempt) || tag.id === current.id || !tag.active;
        button.addEventListener("click", () => replaceInlineTag(hover.publicationId, hover.sourceTagId, tag)); row.append(button);
      }); paths.append(row);
    }
    if (ancestors.hasMore) paths.append(node("small", "", "还有其他归属路径，可在上级中逐层展开。")); tagHoverPanel.append(paths);
    const browseBranch = (id, direction, trail = []) => {
      const list = node("div", "library-tag-hover-branch"), expanded = direction === "up" ? hover.up : hover.down;
      for (const tag of tagHierarchyRows(id, direction)) {
        if (trail.includes(tag.id)) continue;
        const key = [...trail, tag.id].join("/"), children = tagHierarchyRows(tag.id, direction), open = expanded.has(key);
        const row = node("div", "library-tag-hover-row"), arrow = node("button", "library-tag-hover-arrow", children.length ? (open ? "▾" : "▸") : "·");
        arrow.type = "button"; arrow.disabled = !children.length; arrow.setAttribute("aria-label", `${open ? "收起" : "展开"}${tag.name}的${direction === "up" ? "上级" : "下级"}`); arrow.setAttribute("aria-expanded", String(open));
        arrow.addEventListener("click", () => { if (open) expanded.delete(key); else expanded.add(key); renderTagHover(); });
        const name = node("button", "library-tag-hover-name", tag.name); name.type = "button"; name.dataset.replaceTagId = tag.id;
        name.disabled = busy || Boolean(attempt) || !tag.active; name.title = `将本题的「${current.name}」替换为「${tag.name}」`;
        name.addEventListener("click", () => replaceInlineTag(hover.publicationId, hover.sourceTagId, tag));
        row.append(arrow, name, node("small", "library-tag-hover-count", `${Number(tag.subtree_count ?? tag.count) || 0} 题`)); list.append(row);
        if (open) list.append(browseBranch(tag.id, direction, [...trail, tag.id]));
      }
      return list;
    };
    for (const [direction, title] of [["up", "上级 · 可泛化"], ["down", "下级 · 可细化"]]) {
      const section = node("section", "library-tag-hover-section"); section.append(node("h4", "", title));
      const branch = browseBranch(current.id, direction, [current.id]);
      section.append(branch.childNodes.length ? branch : node("p", "helper", direction === "up" ? "已是最上级" : "暂时没有下级")); tagHoverPanel.append(section);
    }
    const methods = (current.related_method_ids || []).map(hierarchyTag).filter(tag => tag?.active);
    if (current.kind === "knowledge" && methods.length) {
      const section = node("section", "library-tag-hover-section"); section.append(node("h4", "", "相关方法 · 添加到本题"));
      for (const method of methods) {
        const button = node("button", "button button-small library-tag-hover-method", `＋ ${method.name}`); button.type = "button"; button.disabled = busy || Boolean(attempt);
        button.addEventListener("click", () => { closeTagHover(); void openTagEditor(state.catalog.get(hover.publicationId), { query: method.name }); }); section.append(button);
      } tagHoverPanel.append(section);
    }
    if (link.rationale) tagHoverPanel.append(node("p", "library-tag-hover-rationale", link.rationale));
    const saved = inlineTagStates.get(hover.publicationId), footer = node("footer", "library-tag-hover-footer");
    if (saved?.message) footer.append(node("span", "library-tag-hover-status", saved.message));
    if (saved?.operationId) { const undo = node("button", "button button-small", "撤销"); undo.type = "button"; undo.disabled = busy; undo.addEventListener("click", () => undoInlineTag(item)); footer.append(undo); }
    if (attempt) { const retry = node("button", "button button-small", "核实上次操作"); retry.type = "button"; retry.disabled = busy; retry.addEventListener("click", () => submitInlineTagAttempt(item, attempt)); footer.append(retry); }
    const find = node("button", "button button-quiet button-small", "查找同标签题目"); find.type = "button"; find.disabled = busy || Boolean(attempt); find.addEventListener("click", () => { closeTagHover(); browseTag(current); }); footer.append(find);
    const map = node("button", "button button-quiet button-small", "查看标签地图"); map.type = "button"; map.id = "libraryTagMapEntry"; map.disabled = busy || Boolean(attempt);
    map.addEventListener("click", () => {
      const context = { publicationId: hover.publicationId, tagId: hover.sourceTagId, label: `第 ${item?.number || ""} 题`, source: item?.source_filename || "" };
      closeTagHover(); void openTagManager({ view: "map", tagId: current.id, questionContext: context });
    }); footer.append(map);
    tagHoverPanel.append(footer); tagHoverPanel.scrollTop = scrollTop; positionTagHover();
  }
  async function replaceInlineTag(publicationId, fromTagId, toTag) {
    const item = state.catalog.get(publicationId); if (!item || fromTagId === toTag.id || inlineTagLocks.has(publicationId)) return false;
    const prior = inlineTagStates.get(publicationId); if (prior?.attempt) { toast("先核实上次标签操作，再继续替换。", "error"); return false; }
    const fromTag = item.tag_assignments?.find(link => link.tag.id === fromTagId)?.tag;
    if (!fromTag || fromTag.kind !== toTag.kind) { toast("只能校正同一类型的标签；相关方法请使用添加入口。", "error"); return false; }
    if (tagHover) tagHover.pinned = true;
    return submitInlineTagAttempt(item, { tagId: fromTagId, tag: fromTag, message: `已将「${fromTag.name}」改为「${toTag.name}」`, payload: {
      publication_ids: [publicationId], replace: { from_tag_id: fromTagId, to_tag_id: toTag.id },
      expected_revisions: { [publicationId]: item.tag_revision }, expected_catalogue_revision: tagCatalogue.revision, request_id: tagRequestId() } });
  }
  document.addEventListener("pointerdown", event => {
    if (!tagHover || tagHoverPanel?.contains(event.target) || event.target.closest("[data-tag-hover]")) return;
    closeTagHover();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || event.isComposing || !tagHover) return;
    event.preventDefault(); event.stopImmediatePropagation(); closeTagHover({ restoreFocus: true });
  }, true);
  window.addEventListener("resize", positionTagHover);
  window.addEventListener("scroll", positionTagHover, { passive: true, capture: true });
  function tagTarget(item) { return item ? [item.id] : Array.from(state.selected); }
  function ensureTagDialog() {
    if (tagDialog) return tagDialog;
    tagDialog = node("dialog", "tag-picker-dialog"); tagDialog.id = "tagPickerDialog";
    const header = node("header", "tag-picker-head"), title = node("div");
    const heading = node("h2", "", "添加标签"); heading.id = "tagPickerTitle";
    const subtitle = node("p", "tag-picker-subtitle", "点击标签立即保存；再次点击移除。"); subtitle.id = "tagPickerSubtitle";
    title.append(heading, subtitle);
    const close = node("button", "button", "关闭"); close.type = "button"; close.addEventListener("click", function() { tagDialog.close(); });
    header.append(title, close);
    const tools = node("div", "tag-picker-tools");
    [["tagPickerCreateTag", "新建标签", () => openTagCatalogueEditor(null, true)]].forEach(function(entry) {
      const button = node("button", "button button-small", entry[1]); button.id = entry[0]; button.type = "button";
      button.addEventListener("click", entry[2]); tools.append(button);
    });
    const search = node("input", "tag-picker-search"); search.id = "tagPickerSearch"; search.type = "search"; search.placeholder = "搜索名称、别名或说明"; search.addEventListener("input", renderTagPicker);
    const quick = node("nav", "tag-picker-quick"); quick.id = "tagPickerQuick";
    const body = node("div", "tag-picker-body"), groups = node("nav", "tag-picker-groups"), list = node("div", "tag-picker-list");
    groups.id = "tagPickerGroups"; list.id = "tagPickerList"; body.append(groups, list);
    const footer = node("footer", "tag-picker-foot"), status = node("span", "tag-picker-save-state"), undo = node("button", "button button-small", "撤销上次修改");
    status.id = "tagPickerStatus"; undo.id = "tagPickerUndo"; undo.type = "button"; undo.hidden = true; undo.addEventListener("click", undoTagOperation); footer.append(status, undo);
    const retry = node("button", "button button-small", "核实上次操作"); retry.id = "tagPickerRetry"; retry.type = "button"; retry.hidden = true;
    retry.addEventListener("click", () => { if (pickerAttempt) void toggleTag(pickerAttempt.tag, pickerAttempt.remove, { attempt: pickerAttempt }); }); footer.append(retry);
    const now = node("div", "tag-picker-now"); now.id = "tagPickerNow";
    const more = node("div", "tag-picker-more"); more.id = "tagPickerMore";
    const moreTitle = node("h3", "tag-picker-block-title");
    moreTitle.append(node("b", "", "想加的"), node("small", "", "点一下贴上，再点一下拿掉"));
    more.append(moreTitle, tools);
    tagDialog.append(header, now, more, search, quick, body, footer); document.body.append(tagDialog); return tagDialog;
  }
  async function openTagEditor(item, options = {}) {
    if (item && inlineTagStates.get(item.id)?.attempt) { toast("先核实上次标签操作，再添加其他标签。", "error"); return; }
    const dialog = ensureTagDialog(); tagTargetIds = tagTarget(item); if (!tagTargetIds.length) return;
    if (pickerAttempt && JSON.stringify(tagTargetIds) !== JSON.stringify(pickerAttempt.payload.publication_ids)) {
      tagTargetIds = pickerAttempt.payload.publication_ids;
      toast("先核实上次标签操作，再切换操作范围。", "error");
    }
    if (tagTargetIds.length > 500) { toast("一次最多整理 500 题，请减少勾选后重试。", "error"); return; }
    $("tagPickerTitle").textContent = pickerAttempt ? `核实上次标签操作（${tagTargetIds.length} 题）` : item ? `${sourceLocator(item)}的标签` : `整理勾选的 ${tagTargetIds.length} 题标签`;
    $("tagPickerSubtitle").textContent = item
      ? "已经贴上的和想加的，都在这个窗口里；要采纳 AI 的建议，在右边详情面板上做。" : "点一下贴上，再点一下拿掉。";
    tagDialog.classList.add("loading");
    try {
      const ids = tagTargetIds.join(","), pair = await Promise.all([loadTagCatalogue(), tagRequest("/api/library/tags/assignments/batch?ids=" + encodeURIComponent(ids))]);
      tagRevisions = {}; tagMemberships = {};
      Object.entries(pair[1].items || {}).forEach(function(entry) {
        tagRevisions[entry[0]] = entry[1].revision;
        tagMemberships[entry[0]] = new Set(entry[1].assignments.filter(function(row) { return !row.needs_review; }).map(function(row) { return row.tag.id; }));
      });
      tagActiveSection = "all"; tagPickerView = "all"; tagUndoId = ""; $("tagPickerSearch").value = options.query || "";
      $("tagPickerStatus").textContent = tagTargetIds.length === 1 ? "单题" : "已选 " + tagTargetIds.length + " 题"; $("tagPickerUndo").hidden = true;
      $("tagPickerRetry").hidden = !pickerAttempt;
      renderTagPicker(); if (!dialog.open) dialog.showModal(); $("tagPickerSearch").focus();
    } catch (error) { toast(error.message + " 请刷新后重试。", "error"); }
    finally { tagDialog.classList.remove("loading"); }
  }
  function renderTagPickerNow() {
    // 这个窗口只管「改标签」：已经贴上的、想加的。
    // 「核对 AI 建议」是完整一件事 —— 看它建议了什么、觉得名字不对就改、然后采纳或拒绝。
    // 它以前被拆成两半：右侧面板只给一个「去核对」的按钮，其余都在这个窗口里，
    // 于是看建议要点一次、改完名再采纳又要跳回来。
    // 现在整件事都在右边详情面板上做完，这个窗口不再重复摆一套 ——
    // 同一个决定只有一个地方能点，两边各点一次还会打架。
    const box = $("tagPickerNow"); if (!box) return;
    box.replaceChildren();
    if (tagTargetIds.length !== 1) { box.hidden = true; return; }
    box.hidden = false;
    const id = tagTargetIds[0], item = state.catalog.get(id);
    if (!item) return;
    const saving = tagDialog.classList.contains("saving");
    const owned = Array.from(tagMemberships[id] || [])
      .map(tagId => (tagCatalogue.tags || []).find(tag => tag.id === tagId)).filter(Boolean);
    const current = node("section", "tag-picker-block");
    const currentTitle = node("h3", "tag-picker-block-title");
    currentTitle.append(node("b", "", "已经在这道题上"), node("small", "", owned.length ? owned.length + " 个" : "还没有标签"));
    const chips = node("div", "tag-picker-current");
    if (!owned.length) chips.append(node("p", "tag-picker-hint", "还没有标签。往下挑一个，或者直接采纳 AI 的建议。"));
    owned.forEach(function(tag) {
      const chip = node("span", "tag-picker-current-chip");
      if (tag.color) chip.style.setProperty("--tag-color", tag.color);
      chip.append(node("span", "", tag.name));
      const drop = node("button", "tag-picker-current-drop", "×"); drop.type = "button";
      drop.title = "从这道题移除「" + tag.name + "」"; drop.setAttribute("aria-label", drop.title);
      drop.disabled = saving; drop.addEventListener("click", () => void toggleTag(tag, true));
      chip.append(drop); chips.append(chip);
    });
    current.append(currentTitle, chips); box.append(current);
    // 这里不摆建议清单本身：一份同样的内容出现在两个地方，看两遍还得对得上两份，
    // 改了一处另一处就过期了。这里只留一条指路，说明去哪儿核对。
    if (!item.tag_suggestion) return;
    const suggestion = item.tag_suggestion;
    const pendingCount = suggestion.status === "pending"
      ? (suggestion.suggestions || []).filter(entry => !entry.decision).length : 0;
    const ai = node("section", "tag-picker-block");
    const aiTitle = node("h3", "tag-picker-block-title");
    const note = pendingCount ? pendingCount + " 条待核对 · 在右边详情面板上核对"
      : { queued: "排队中", running: "生成中", review: "暂不能判断", no_match: "没有合适的标签", failed: "生成失败", stale: "依据已变化" }[suggestion.status] || "";
    aiTitle.append(node("b", "", "AI 建议的"), node("small", "", note));
    const go = node("button", "button button-small", "去右边面板核对");
    go.type = "button";
    go.title = "关掉这个窗口，右边详情面板上会直接列出 AI 建议的标签，可以改名、采纳或拒绝";
    go.addEventListener("click", function() { dialog.close(); focusInspectorTag(item); });
    ai.append(aiTitle, go); box.append(ai);
  }
  const kindLabels = { knowledge: "知识点", method: "解题方法", custom: "自定义", legacy: "历史标签" };
  function tagChildrenIndex() {
    const children = new Map();
    (tagCatalogue.tags || []).forEach(function(tag) {
      (tag.parent_ids || []).forEach(function(pid) {
        if (!children.has(pid)) children.set(pid, []);
        children.get(pid).push(tag.id);
      });
    });
    return children;
  }
  function tagSubtree(id, children) {
    const seen = new Set([id]), todo = [id];
    while (todo.length) (children.get(todo.pop()) || []).forEach(function(child) { if (!seen.has(child)) { seen.add(child); todo.push(child); } });
    return seen;
  }
  function tagTrail(tag, byId) {
    const names = [], seen = new Set([tag.id]), todo = [...(tag.parent_ids || [])];
    while (todo.length) {
      const parent = byId.get(String(todo.pop()));
      if (!parent || seen.has(parent.id)) continue;
      seen.add(parent.id); names.unshift(parent.name); todo.push(...(parent.parent_ids || []));
    }
    return names;
  }
  function renderTagPicker() {
    renderTagPickerNow();
    if (!tagDialog) return;
    const groupBox = $("tagPickerGroups"), list = $("tagPickerList"), quick = $("tagPickerQuick"); if (!groupBox || !list || !quick) return;
    const groupScrollTop = groupBox.scrollTop, groupScrollLeft = groupBox.scrollLeft;
    quick.replaceChildren();
    [{ id: "all", name: "全部标签" }, { id: "favorite", name: "收藏" }, { id: "recent", name: "最近使用" }].forEach(function(view) {
      const button = node("button", "tag-group-button" + (tagPickerView === view.id ? " active" : ""), view.name);
      button.type = "button"; button.addEventListener("click", function() { tagPickerView = view.id; tagActiveSection = "all"; renderTagPicker(); }); quick.append(button);
    });
    const byId = new Map((tagCatalogue.tags || []).map(function(tag) { return [String(tag.id), tag]; }));
    const children = tagChildrenIndex();
    groupBox.replaceChildren();
    const allButton = node("button", "tag-group-button" + (tagActiveSection === "all" ? " active" : ""), "全部章节");
    allButton.type = "button"; allButton.addEventListener("click", function() { tagPickerView = "all"; tagActiveSection = "all"; renderTagPicker(); }); groupBox.append(allButton);
    (tagCatalogue.tags || []).filter(function(tag) {
      return tag.active !== false && !(tag.parent_ids || []).some(function(pid) { return byId.has(String(pid)); });
    }).sort(function(left, right) {
      return (kindLabels[left.kind] || "").localeCompare(kindLabels[right.kind] || "", "zh-CN")
        || left.name.localeCompare(right.name, "zh-CN");
    }).forEach(function(tag) {
      const button = node("button", "tag-group-button" + (tagActiveSection === String(tag.id) ? " active" : ""), tag.name);
      button.title = (kindLabels[tag.kind] || "标签") + " · 连同下面 " + (tagSubtree(String(tag.id), children).size - 1) + " 个下级标签";
      button.type = "button"; button.addEventListener("click", function() { tagPickerView = "all"; tagActiveSection = String(tag.id); renderTagPicker(); }); groupBox.append(button);
    });
    groupBox.scrollTop = groupScrollTop; groupBox.scrollLeft = groupScrollLeft;
    const query = String($("tagPickerSearch").value || "").trim().normalize("NFKC").toLocaleLowerCase();
    let recentIds = [];
    try { recentIds = JSON.parse(localStorage.getItem("qb-library-recent-tags") || "[]"); } catch (_) {}
    const active = tagActiveSection === "all" ? null : tagSubtree(tagActiveSection, children);
    const rows = (tagCatalogue.tags || []).filter(function(tag) {
      if (!tag.active) return false;
      if (tagPickerView === "favorite" && !tag.favorite) return false;
      if (tagPickerView === "recent" && !recentIds.includes(tag.id)) return false;
      if (!query && tagPickerView === "all" && active && !active.has(String(tag.id))) return false;
      const trail = tagTrail(tag, byId).join(" ");
      const text = [tag.name, tag.description, tag.conditions, ...(tag.aliases || []), trail].join(" ").normalize("NFKC").toLocaleLowerCase();
      return !query || text.includes(query);
    });
    if (tagPickerView === "recent") rows.sort(function(left, right) { return recentIds.indexOf(right.id) - recentIds.indexOf(left.id); });
    const scrollTop = list.scrollTop;
    list.replaceChildren(); if (!rows.length) {
      list.append(node("p", "library-empty", query ? "没有找到这个标签。" : "还没有可用标签。"));
      const create = node("button", "button button-small", query ? `创建「${$("tagPickerSearch").value.trim()}」并添加` : "新建标签"); create.type = "button";
      create.addEventListener("click", () => openTagCatalogueEditor(null, true)); list.append(create);
      list.scrollTop = scrollTop; return;
    }
    rows.forEach(function(tag) {
      const count = tagTargetIds.reduce(function(total, id) { return total + (tagMemberships[id] && tagMemberships[id].has(tag.id) ? 1 : 0); }, 0);
      const single = tagTargetIds.length === 1;
      const text = single ? (count ? "✓ 已添加" : "添加") : count === tagTargetIds.length ? "全部都有" : count ? "部分有 " + count + "/" + tagTargetIds.length : "全部没有";
      const button = node("button", "tag-picker-row" + (count === tagTargetIds.length ? " active" : count ? " mixed" : ""));
      if (tag.color) button.style.setProperty("--tag-color", tag.color);
      button.type = "button"; button.disabled = tagDialog.classList.contains("saving");
      const title = node("span", "tag-picker-row-title");
      title.append(node("strong", "", tag.name), node("small", "", (kindLabels[tag.kind] || "标签") + (tagTrail(tag, byId).length ? " · " + tagTrail(tag, byId).join(" › ") : "")));
      button.append(title, node("span", "tag-picker-row-detail", tag.description || tag.conditions || ""), node("span", "tag-picker-row-state", text));
      button.addEventListener("click", function() { void toggleTag(tag, count === tagTargetIds.length); });
      if (!single && count > 0 && count < tagTargetIds.length) {
        const mixed = node("div", "tag-picker-mixed-row"), remove = node("button", "button button-small", "全部移除"); remove.type = "button";
        button.querySelector(".tag-picker-row-state").textContent = text + " · 全部添加";
        remove.disabled = tagDialog.classList.contains("saving"); remove.addEventListener("click", () => void toggleTag(tag, true)); mixed.append(button, remove); list.append(mixed);
      } else list.append(button);
    });
    list.scrollTop = scrollTop;
  }
  async function toggleTag(tag, remove, options = {}) {
    if (tagDialog.classList.contains("saving")) return false; tagDialog.classList.add("saving");
    try {
      if (pickerAttempt && !options.attempt) { toast("上次保存结果尚未核实，请点“核实上次操作”。", "error"); return false; }
      let scope = options.scope || { whole: true };
      if (!remove && tag.kind === "method" && !options.scope && !options.attempt?.payload) {
        const answer = window.prompt("这个方法用于整题，还是只用于某些小问？留空表示整题；例如输入 1,2 表示第 1、2 问。", "");
        if (answer === null) return false;
        if (answer.trim()) {
          const parts = answer.split(/[，,、\s]+/).filter(Boolean);
          if (parts.some(function(part) { return !/^[1-9][0-9]?$/.test(part); }) || new Set(parts).size !== parts.length) {
            toast("请输入 1 到 99 的不重复小问编号。", "error"); return false;
          }
          scope = { subquestions: parts };
        }
      }
      const payload = options.attempt?.payload || {
        publication_ids: tagTargetIds, add: remove ? [] : [tag.id], remove: remove ? [tag.id] : [],
        scopes: remove ? {} : { [tag.id]: scope }, expected_revisions: { ...tagRevisions }, request_id: tagRequestId()
      };
      if (options.attempt && !options.attempt.payload) options.attempt.payload = payload;
      pickerAttempt = { tag, remove, payload }; $("tagPickerRetry").hidden = true;
      const result = await tagRequest("/api/library/tags/assignments/batch", { method: "POST", body: JSON.stringify(payload) });
      pickerAttempt = null;
      payload.publication_ids.forEach(function(id) { tagRevisions[id] = result.revisions[id]; if (remove) tagMemberships[id]?.delete(tag.id); else tagMemberships[id]?.add(tag.id); });
      let refreshFailed = false;
      try {
        const current = await tagRequest("/api/library/tags/assignments/batch?ids=" + encodeURIComponent(payload.publication_ids.join(",")));
        Object.entries(current.items || {}).forEach(([id, row]) => { tagRevisions[id] = row.revision; tagMemberships[id] = new Set(row.assignments.filter(link => !link.needs_review).map(link => link.tag.id)); });
      } catch (_) { refreshFailed = true; }
      tagUndoId = result.operation_id; $("tagPickerUndo").hidden = false;
      $("tagPickerStatus").textContent = remove ? "已从本次选题移除「" + tag.name + "」" : "已添加「" + tag.name + "」";
      if (refreshFailed) $("tagPickerStatus").textContent += "；已保存，当前状态载入失败，请重新打开标签窗口";
      try { let recent = JSON.parse(localStorage.getItem("qb-library-recent-tags") || "[]").filter(function(id) { return id !== tag.id; }); recent.push(tag.id); localStorage.setItem("qb-library-recent-tags", JSON.stringify(recent.slice(-20))); } catch (_) {}
      renderTagPicker(); await refreshTaggedPublications(payload.publication_ids, result);
      return true;
    } catch (error) {
      if (error.status && error.status < 500) pickerAttempt = null;
      $("tagPickerRetry").hidden = !pickerAttempt;
      if (options.throwOnError) throw error;
      toast(error.message + (pickerAttempt ? " 请核实上次操作，已保留原请求。" : " 请刷新标签后重试。"), "error"); return false;
    }
    finally { tagDialog.classList.remove("saving"); renderTagPicker(); }
  }
  async function undoTagOperation() {
    if (!tagUndoId) return;
    try { const result = await tagRequest("/api/library/tags/undo", { method: "POST", body: JSON.stringify({ operation_id: tagUndoId }) });
      const ids = tagTargetIds.slice(); tagUndoId = ""; await refreshTaggedPublications(ids, result);
      const first = ids.length === 1 ? state.catalog.get(ids[0]) : null; await openTagEditor(first);
    } catch (error) { toast(error.message + " 请刷新后重试。", "error"); }
  }
  async function catalogueAction(action, fields) {
    if (catalogueUnknown) throw new Error("上次目录操作结果尚未核实，请先点“核实上次目录操作”");
    const signature = JSON.stringify({ action, ...fields, revision: tagCatalogue.revision });
    const payload = catalogueAttempts.get(signature) || { action, ...fields, revision: tagCatalogue.revision, request_id: tagRequestId() };
    catalogueAttempts.set(signature, payload);
    let result;
    try { result = await tagRequest("/api/library/tags", { method: "POST", body: JSON.stringify(payload) }); catalogueAttempts.delete(signature); }
    catch (error) {
      if (error.status && error.status < 500) catalogueAttempts.delete(signature);
      else { catalogueUnknown = payload; if ($("tagCatalogueRetry")) $("tagCatalogueRetry").hidden = false; }
      throw error;
    }
    tagCatalogue = result.catalogue || await loadTagCatalogue();
    if (tagDialog?.open && result.operation_id) {
      tagUndoId = result.operation_id; $("tagPickerUndo").hidden = false;
      $("tagPickerStatus").textContent = "已保存，可撤销";
    }
    if (tagManagerDialog) void loadTagOperationHistory();
    return result;
  }
  async function deleteCatalogueTag(tag) {
    try {
      const impact = await tagRequest("/api/library/tags/impact?action=delete_tag&tag_id=" + encodeURIComponent(tag.id));
      if (impact.assignment_count && !window.confirm(`删除标签「${tag.name}」？这会从 ${impact.question_count} 道当前题和 ${impact.historical_count} 道历史版本中移除，共 ${impact.assignment_count} 条关联。题面和答案不变，操作可撤销。`)) return false;
      const result = await catalogueAction("delete_tag", { tag_id: tag.id, impact_token: impact.token, confirmed: true });
      await loadTagCatalogue(); renderTagManager(); await loadTagOperationHistory();
      if (tagDialog?.open) renderTagPicker();
      toast("标签已从目录和题目关联中删除，可在操作记录中撤销。", "success");
      return result;
    } catch (error) { toast(error.message + " 标签及题目内容没有被覆盖。", "error"); return false; }
  }
  async function startEmptyTagCatalogue() {
    try {
      const impact = await tagRequest("/api/library/tags/impact?action=reset_empty");
      const details = impact.question_count || impact.historical_count
        ? `\n将清除 ${impact.tag_count} 个标签和 ${impact.question_count} 道当前题目的 ${impact.assignment_count} 条关联；另有 ${impact.historical_count} 道历史版本受影响。操作可撤销。`
        : "\n当前目录为空。确认后会标记为已初始化，不会自动导入系统标签。";
      if (!window.confirm((impact.tag_count ? "清空标签目录？" : "从空白目录开始？") + details)) return false;
      const result = await catalogueAction("reset_empty", { impact_token: impact.token, confirmed: true });
      if (tagDialog?.open) { renderTagPicker(); }
      if (tagManagerDialog?.open) { renderTagManager(); await loadTagOperationHistory(); }
      toast("目录已清空，可从头建立；需要时可以撤销。", "success");
      if (result.operation_id && tagDialog?.open) { tagUndoId = result.operation_id; $("tagPickerUndo").hidden = false; }
      return result;
    } catch (error) { toast(error.message + " 目录没有被覆盖。", "error"); return false; }
  }
  async function openTagTemplates() {
    try {
      const data = await tagRequest("/api/library/tags/templates");
      const dialog = node("dialog", "tag-picker-dialog tag-template-dialog"); tagTemplateDialog = dialog;
      const header = node("header", "tag-picker-head"), intro = node("div");
      intro.append(node("h2", "", "系统模板"), node("p", "tag-picker-subtitle", "预览不会修改目录。导入会增补所选章节的层级关系，保留现有改名和上下级；需要的上级会一并导入。"));
      const close = node("button", "button", "关闭"); close.type = "button"; header.append(intro, close);
      const packages = node("nav", "tag-template-packages");
      const sectionChecks = new Map();
      const blueprint = new Map((data.tags || []).map(tag => [tag.key, tag]));
      const templatePaths = tag => {
        const paths = [], todo = [{ tag, trail: [] }];
        while (todo.length && paths.length < 6) {
          const current = todo.pop(); if (current.trail.some(value => value.key === current.tag.key)) continue;
          const path = [current.tag, ...current.trail], parents = (current.tag.parent_keys || []).map(key => blueprint.get(key)).filter(Boolean);
          if (!parents.length) paths.push(path.map(value => value.name).join(" › "));
          else parents.forEach(parent => todo.push({ tag: parent, trail: path }));
        }
        return paths;
      };
      const dependencies = node("section", "tag-template-dependencies"); dependencies.id = "tagTemplateDependencies";
      const updateTemplatePreview = () => {
        const sections = new Set(Array.from(sectionChecks.entries()).filter(([, check]) => check.checked).map(([key]) => key));
        const direct = new Set((data.tags || []).filter(tag => tag.section_keys.some(key => sections.has(key))).map(tag => tag.key));
        const included = new Set(direct), todo = Array.from(direct);
        while (todo.length) {
          const tag = blueprint.get(todo.pop());
          (tag?.parent_keys || []).forEach(key => { if (!included.has(key) && blueprint.has(key)) { included.add(key); todo.push(key); } });
        }
        const extra = Array.from(included).filter(key => !direct.has(key)).map(key => blueprint.get(key));
        dependencies.replaceChildren();
        if (!sections.size) { dependencies.append(node("p", "helper", "勾选章节后，这里会显示同时导入的必要上级。")); return; }
        dependencies.append(node("h4", "", `所选章节 ${direct.size} 个标签；必要上级 ${extra.length} 个`));
        if (extra.length) {
          const names = node("div", "tag-template-parent-list");
          extra.forEach(tag => { const chip = node("span", "", tag.name); chip.title = templatePaths(tag).join("\n"); names.append(chip); }); dependencies.append(names);
          dependencies.append(node("p", "helper", "这些上级标签会一并导入；不会额外导入它们所在的未选章节。"));
        }
      };
      (data.packages || []).forEach(function(pkg) {
        const button = node("button", "button button-small", pkg.name + "（" + pkg.tag_count + " 个标签）"); button.type = "button";
        button.addEventListener("click", function() { pkg.section_keys.forEach(function(key) { if (sectionChecks.has(key)) sectionChecks.get(key).checked = true; }); updateTemplatePreview(); }); packages.append(button);
      });
      const allRow = node("label", "tag-template-all"), all = node("input"); all.type = "checkbox";
      all.addEventListener("change", function() { sectionChecks.forEach(function(check) { check.checked = all.checked; }); updateTemplatePreview(); });
      allRow.append(all, node("span", "", "全选所有章节"));
      const list = node("div", "tag-template-list");
      (data.sections || []).forEach(function(section) {
        const detail = node("details", "tag-template-group"), summary = node("summary"), check = node("input"); check.type = "checkbox";
        check.addEventListener("click", function(event) { event.stopPropagation(); });
        check.addEventListener("change", updateTemplatePreview);
        const sectionTags = (data.tags || []).filter(function(tag) { return tag.section_keys.includes(section.key); });
        summary.append(check, node("strong", "", section.name), node("small", "", sectionTags.length + " 个标签"));
        sectionChecks.set(section.key, check);
        const preview = node("div", "tag-template-preview");
        sectionTags.forEach(function(tag) {
          const row = node("p"); row.append(node("b", "", tag.name), node("small", "tag-template-path", templatePaths(tag).join("；")), node("span", "", tag.description || ""));
          const methods = (tag.related_method_keys || []).map(key => blueprint.get(key)?.name).filter(Boolean);
          if (methods.length) row.append(node("small", "tag-template-related", "相关方法：" + methods.join("、") + "（只连接目录中已有或本次选中的方法）"));
          preview.append(row);
        });
        detail.append(summary, preview); list.append(detail);
      });
      const body = node("div", "tag-template-body"); body.append(packages, allRow, dependencies, list); updateTemplatePreview();
      const footer = node("footer", "tag-picker-foot"), status = node("span", "helper", "模板未勾选。"), cancel = node("button", "button", "取消"), apply = node("button", "button button-primary", "导入所选章节");
      cancel.type = "button"; apply.type = "button"; footer.append(status, cancel, apply);
      dialog.append(header, body, footer); document.body.append(dialog);
      const closeDialog = function() { dialog.close(); };
      close.addEventListener("click", closeDialog); cancel.addEventListener("click", closeDialog);
      dialog.addEventListener("close", function() { dialog.remove(); if (tagTemplateDialog === dialog) tagTemplateDialog = null; });
      apply.addEventListener("click", async function() {
        const keys = Array.from(sectionChecks.entries()).filter(([, input]) => input.checked).map(([key]) => key);
        if (!keys.length) { status.textContent = "先选择一个或多个章节。"; return; }
        apply.disabled = true; status.textContent = "正在导入所选模板…";
        try {
          await catalogueAction("apply_template", { section_keys: keys });
          closeDialog(); await loadTagCatalogue(); renderTagPicker(); if (tagManagerDialog?.open) { renderTagManager(); await loadTagOperationHistory(); }
          toast("已把所选模板加入当前目录。", "success");
        } catch (error) { status.textContent = error.message + "；选择仍保留。"; apply.disabled = false; }
      });
      dialog.showModal();
    } catch (error) { toast(error.message, "error"); }
  }
  function openTagCatalogueEditor(record = null, fromPicker = false) {
    const defaults = typeof fromPicker === "object" ? fromPicker : {};
    fromPicker = fromPicker === true;
    if (tagCatalogueEditorDialog?.open || tagDialog?.classList.contains("saving")) return;
    const editing = Boolean(record), label = "标签";
    const dialog = node("dialog", "tag-catalogue-editor-dialog"); dialog.id = "tagCatalogueEditorDialog";
    tagCatalogueEditorDialog = dialog;
    const header = node("header", "tag-picker-head"), heading = node("div");
    heading.append(node("h2", "", (editing ? "编辑" : "新建") + label), node("p", "tag-picker-subtitle",
      "自己建立标签，用上级和下级分层；一个标签可以有多个上级，也可以没有上级。"));
    const close = node("button", "button", "关闭"); close.type = "button"; header.append(heading, close);
    const form = node("form", "tag-catalogue-editor-form"); form.id = "tagCatalogueEditorForm";
    const body = node("div", "tag-catalogue-editor-body");
    const field = function(title, id, control, wide = false) {
      const row = node("label", "tag-catalogue-field" + (wide ? " tag-catalogue-wide" : ""));
      control.id = id; row.append(node("span", "", title), control); body.append(row); return control;
    };
    const name = field(label + "名称", "tagCatalogueName", node("input"), true);
    name.required = true; name.maxLength = 80; name.value = record?.name || (fromPicker ? $("tagPickerSearch")?.value.trim() : "") || "";
    name.placeholder = "例如：课堂例题、易错题";
    let kind, description, aliases, conditions, scope, levelChoices, parentChoices, relatedChoices;
    kind = field("标签类型", "tagCatalogueKind", node("select"));
    [["custom", "自定义标签"], ["knowledge", "知识点"], ["method", "解题方法"]].forEach(function(entry) { kind.add(new Option(entry[1], entry[0])); });
    if (record?.kind === "legacy") kind.add(new Option("历史标签", "legacy"));
    kind.value = record?.kind || defaults.kind || "custom"; kind.disabled = editing;
    const color = field("颜色（可选）", "tagCatalogueColor", node("select"));
    [["", "默认"], ["#bb575d", "红色"], ["#4f8278", "绿色"], ["#477a9e", "蓝色"], ["#8965a5", "紫色"], ["#bf8545", "橙色"], ["#438b94", "青色"]].forEach(function(entry) { color.add(new Option(entry[1], entry[0])); });
    if (record?.color && !Array.from(color.options).some(function(option) { return option.value === record.color; })) color.add(new Option("当前颜色", record.color));
    color.value = record?.color || "";
    description = field("简短说明（可选）", "tagCatalogueDescription", node("textarea"), true);
    description.rows = 2; description.maxLength = 300; description.value = record?.description || "";
    parentChoices = field("上级标签（可多选；留空作为顶级）", "tagCatalogueParents", node("select"), true); parentChoices.multiple = true; parentChoices.size = 5;
    const chosenParents = record?.parent_ids || defaults.parent_ids || [];
    const renderParents = () => {
      const selected = parentChoices.options.length ? Array.from(parentChoices.selectedOptions).map(option => option.value) : chosenParents;
      parentChoices.replaceChildren();
      (tagCatalogue.tags || []).filter(tag => tag.active && tag.kind === kind.value && tag.id !== record?.id).forEach(tag => {
        const option = new Option(tag.name, tag.id); option.selected = selected.includes(tag.id); parentChoices.append(option);
      });
    };
    renderParents(); kind.addEventListener("change", renderParents);
    relatedChoices = field("相关方法（知识与方法的联系，不作为上级）", "tagCatalogueRelatedMethods", node("select"), true); relatedChoices.multiple = true; relatedChoices.size = 4;
    (tagCatalogue.tags || []).filter(tag => tag.active && tag.kind === "method").forEach(tag => {
      const option = new Option(tag.name, tag.id); option.selected = (record?.related_method_ids || []).includes(tag.id); relatedChoices.append(option);
    });
    const syncRelated = () => { relatedChoices.parentElement.hidden = kind.value !== "knowledge"; };
    kind.addEventListener("change", syncRelated); syncRelated();
    scope = field("适用小问（留空表示整题）", "tagCatalogueScope", node("input"), true); scope.placeholder = "例如：1,2";
    const syncScope = function() { scope.parentElement.hidden = !(fromPicker && !editing && kind.value === "method"); };
    kind.addEventListener("change", syncScope); syncScope();
    const advanced = node("details", "tag-catalogue-wide"); advanced.append(node("summary", "", "别名、学段与适用条件"));
    const advancedFields = node("div", "tag-catalogue-editor-body");
    const extra = function(title, control) { const row = node("label", "tag-catalogue-field tag-catalogue-wide"); row.append(node("span", "", title), control); advancedFields.append(row); return control; };
    aliases = extra("别名（用逗号分隔）", node("input")); aliases.value = (record?.aliases || []).join("，");
    conditions = extra("适用条件（可选）", node("textarea")); conditions.rows = 2; conditions.maxLength = 2000; conditions.value = record?.conditions || "";
    levelChoices = node("fieldset", "tag-catalogue-wide"); levelChoices.append(node("legend", "", "适用学段"));
    ["初中", "高中"].forEach(function(level) { const row = node("label", "tag-catalogue-check"), check = node("input"); check.type = "checkbox"; check.value = level; check.checked = (record?.levels || ["初中", "高中"]).includes(level); row.append(check, node("span", "", level)); levelChoices.append(row); });
    advancedFields.append(levelChoices); advanced.append(advancedFields); body.append(advanced);
    const status = node("p", "tag-catalogue-status"); status.id = "tagCatalogueStatus"; status.setAttribute("role", "status");
    const footer = node("footer", "tag-picker-foot"), cancel = node("button", "button", "取消"), save = node("button", "button button-primary", editing ? "保存修改" : fromPicker ? "创建并添加" : "创建" + label);
    cancel.type = "button"; save.type = "submit"; save.id = "tagCatalogueSave"; save.value = fromPicker && !editing ? "apply" : "save";
    const createOnly = node("button", "button", "仅创建标签"); createOnly.type = "submit"; createOnly.id = "tagCatalogueCreateOnly"; createOnly.value = "save"; createOnly.hidden = !(fromPicker && !editing);
    footer.append(cancel, save, createOnly); form.append(body, status, footer); dialog.append(header, form); document.body.append(dialog);
    let saving = false, created = null, attempt = null, applyAttempt = {}, composing = false;
    form.addEventListener("compositionstart", function() { composing = true; });
    form.addEventListener("compositionend", function() { composing = false; });
    form.addEventListener("keydown", function(event) { if (event.key === "Enter" && (composing || event.isComposing || event.keyCode === 229)) event.preventDefault(); });
    const leave = function() { if (!saving) dialog.close(); }; close.addEventListener("click", leave); cancel.addEventListener("click", leave);
    dialog.addEventListener("cancel", function(event) { if (saving) event.preventDefault(); });
    dialog.addEventListener("close", function() { dialog.remove(); if (tagCatalogueEditorDialog === dialog) tagCatalogueEditorDialog = null; });
    const finish = function(savedRecord) {
      if (fromPicker) { tagPickerView = "all"; $("tagPickerSearch").value = savedRecord.name; renderTagPicker(); }
      if (tagManagerDialog?.open) renderTagManager();
      if (tagManagerDialog) void loadTagOperationHistory();
      void load({ quiet: true }); dialog.close();
    };
    form.addEventListener("submit", async function(event) {
      event.preventDefault(); if (saving || composing || event.isComposing) return;
      const shouldApply = event.submitter?.value === "apply";
      let methodScope = { whole: true };
      if (shouldApply && kind?.value === "method" && scope.value.trim()) {
        const parts = scope.value.split(/[，,、\s]+/).filter(Boolean);
        if (parts.length > 12 || parts.some(function(part) { return !/^[1-9][0-9]?$/.test(part); }) || new Set(parts).size !== parts.length) { status.textContent = "请输入不重复的 1 到 99 小问编号，最多十二项。"; return; }
        methodScope = { subquestions: parts };
      }
      const fields = { name: name.value.trim(), color: color.value, description: description.value.trim(),
        aliases: aliases.value.split(/[，,]/).map(function(value) { return value.trim(); }).filter(Boolean), conditions: conditions.value.trim(),
        levels: Array.from(levelChoices.querySelectorAll("input:checked")).map(function(input) { return input.value; }),
        parent_ids: Array.from(parentChoices.selectedOptions).map(option => option.value),
        related_method_ids: kind.value === "knowledge" ? Array.from(relatedChoices.selectedOptions).map(option => option.value) : [] };
      if (editing && fields.name.normalize("NFKC") !== record.name && !fields.aliases.includes(record.name)) fields.aliases.push(record.name);
      if (editing) fields.tag_id = record.id; else fields.kind = kind.value;
      const action = editing ? "update_tag" : "create_tag";
      const fingerprint = JSON.stringify([action, fields]);
      if (!attempt || attempt.fingerprint !== fingerprint) attempt = { fingerprint, payload: { action, ...fields, revision: tagCatalogue.revision, request_id: tagRequestId() } };
      saving = true; close.disabled = cancel.disabled = save.disabled = createOnly.disabled = true;
      status.textContent = created ? "正在添加到题目…" : "正在保存…";
      try {
        if (!created) {
          const result = await tagRequest("/api/library/tags", { method: "POST", body: JSON.stringify(attempt.payload) });
          tagCatalogue = result.catalogue || await loadTagCatalogue();
          const normalizedName = fields.name.normalize("NFKC");
          created = tagCatalogue.tags.find(function(value) { return editing ? value.id === record.id : value.name === normalizedName && value.kind === fields.kind; });
          if (!created) throw new Error("目录已保存，但结果尚未载入，请刷新后查看。");
        }
        if (shouldApply) {
          Array.from(body.querySelectorAll("input, select, textarea")).forEach(function(input) { input.disabled = true; });
          await toggleTag(created, false, { scope: methodScope, throwOnError: true, attempt: applyAttempt });
        } else if (fromPicker) $("tagPickerStatus").textContent = "已创建" + label + "「" + created.name + "」";
        finish(created);
      } catch (error) {
        status.textContent = created ? "标签已创建，添加到题目未完成：" + error.message + "。重试只添加，不会再建标签。" : error.message + "。填写内容已保留。";
        if (created) { save.textContent = "重试添加"; createOnly.textContent = "返回标签列表"; }
      } finally { saving = false; close.disabled = cancel.disabled = save.disabled = createOnly.disabled = false; }
    });
    dialog.showModal(); name.focus();
  }
  async function refreshOpenTagPicker() {
    if (!tagDialog?.open || !tagTargetIds.length || tagDialog.classList.contains("saving")) return;
    try {
      const pair = await Promise.all([loadTagCatalogue(), tagRequest("/api/library/tags/assignments/batch?ids=" + encodeURIComponent(tagTargetIds.join(",")))]);
      Object.entries(pair[1].items || {}).forEach(function(entry) {
        tagRevisions[entry[0]] = entry[1].revision;
        tagMemberships[entry[0]] = new Set(entry[1].assignments.filter(function(row) { return !row.needs_review; }).map(function(row) { return row.tag.id; }));
      });
      renderTagPicker();
    } catch (error) { $("tagPickerStatus").textContent = "目录更新尚未载入：" + error.message; }
  }
  async function openTagManager(context = {}) {
    if (tagDialog?.classList.contains("saving")) return;
    if (tagDialog?.open) tagDialog.close();
    if (window.matchMedia("(max-width: 979px)").matches) setInspectorOpen(false);
    if (document.body.classList.contains("rail-open")) setRail(false);
    try { await ensureTagManagerDialog().show(context); } catch (error) { toast(error.message, "error"); }
  }
  function browseTag(tag) {
    state.tag = ""; state.tagAll = ""; state.tagNot = ""; state.tagAny = tag.id; state.tagMode = "any";
    state.tagReview = false; state.untaggedKnowledge = false;
    syncUrl(); void load();
  }
  function ensureTagManagerDialog() {
    if (tagManagerDialog) return tagManagerDialog;
    tagManagerDialog = window.QBLibraryTagManager.create({
      mount: document.querySelector(".library-main"), loadCatalogue: loadTagCatalogue, action: catalogueAction,
      request: async (url, options) => { const result = await tagRequest(url, options); if (result.catalogue) tagCatalogue = result.catalogue; return result; }, editEntity: (_entity, record, defaults = {}) => openTagCatalogueEditor(record, defaults), openTemplates: openTagTemplates, clearCatalogue: startEmptyTagCatalogue,
      deleteTag: deleteCatalogueTag, browseTag,
      contextValid: context => Boolean(state.catalog.get(context.publicationId)?.tag_assignments?.some(link => link.tag.id === context.tagId && !link.needs_review)),
      replaceForQuestion: (context, tag) => replaceInlineTag(context.publicationId, context.tagId, tag),
      questionSaveState: context => inlineTagStates.get(context.publicationId),
      retryQuestionSave: context => {
        const item = state.catalog.get(context.publicationId), attempt = inlineTagStates.get(context.publicationId)?.attempt;
        return item && attempt ? submitInlineTagAttempt(item, attempt) : false;
      },
      onClose: () => { void load({ quiet: true }); void refreshOpenTagPicker(); }
    });
    const retry = node("button", "button button-small", "核实上次目录操作"); retry.id = "tagCatalogueRetry"; retry.type = "button"; retry.hidden = !catalogueUnknown;
    retry.addEventListener("click", async () => {
      if (!catalogueUnknown || retry.disabled) return; retry.disabled = true;
      try {
        const result = await tagRequest("/api/library/tags", { method: "POST", body: JSON.stringify(catalogueUnknown) });
        catalogueUnknown = null; catalogueAttempts.clear(); retry.hidden = true;
        tagCatalogue = result.catalogue || await loadTagCatalogue(); renderTagManager(); await loadTagOperationHistory(); await load({ quiet: true }); toast("上次目录操作已核实，可继续整理。", "success");
      } catch (error) {
        if (error.status && error.status < 500) { catalogueUnknown = null; catalogueAttempts.clear(); retry.hidden = true; }
        toast(error.message + " 选择和现有内容已保留。", "error");
      } finally { retry.disabled = false; }
    });
    $("tagManagerWorkspace").querySelector(".tag-directory-toolbar").append(retry);
    return tagManagerDialog;
  }
  function renderTagManager() { if (tagManagerDialog) tagManagerDialog.render(tagCatalogue); }
  async function loadTagOperationHistory() {
    if (!tagManagerDialog) return;
    try { const result = await tagRequest("/api/library/tags/operations"); tagOperationHistory = result.operations || []; tagManagerDialog.renderHistory(tagOperationHistory); }
    catch (error) { console.warn("标签操作记录暂时无法载入", error.message); }
  }
  async function resolveTagSuggestion(item, action, panel, indicesOverride = null, retryOriginal = false) {
    const suggestion = item.tag_suggestion; if (!suggestion || suggestion.status !== "pending" || suggestionLocks.has(suggestion.id)) return;
    const selected = indicesOverride || Array.from(panel.querySelectorAll("[data-tag-suggestion='" + suggestion.id + "']:checked")).map(input => Number(input.value));
    if (!selected.length) { toast("先勾选要处理的建议。", "error"); return; }
    const draft = suggestionDraft(item, suggestion), newTags = {};
    if (action === "accept" && !retryOriginal && selected.some(index => suggestion.suggestions[index]?.new_name)) {
      try { await loadTagCatalogue(); } catch (error) { toast(error.message + " 请先载入目录再采纳新标签。", "error"); return; }
      for (const index of selected) {
        const entry = suggestion.suggestions[index], fields = draft[index] || {};
        if (entry.retired_tag) {
          // 旧标签本来就在库里，不用你挑上级；但启用前要你定它属于哪一学段。
          if (!(fields.levels || []).length) {
            const block = panel.querySelector(`[data-suggestion-index="${index}"]`);
            if (block) block.scrollIntoView({ block: "nearest" });
            toast("「" + entry.retired_tag.name + "」是你库里原有的旧标签。重新启用前，请先选它适用哪一学段。", "error"); return;
          }
          continue;
        }
        const existing = tagCatalogue.tags.some(tag => tag.kind === (fields.kind || entry.kind)
          && tag.name.normalize("NFKC") === String(fields.name ?? entry.new_name ?? "").trim().normalize("NFKC"));
        if (entry.new_name && !existing && !(fields.parent_ids ?? entry.parent_ids ?? []).length && fields.top_level !== true && tagCatalogue.tags.length) {
          const edit = panel.querySelector(`[data-suggestion-index="${index}"] .tag-suggestion-edit`); if (edit) edit.open = true;
          toast("新标签请先选择上级，或明确勾选“作为顶级标签”。", "error"); return;
        }
      }
    }
    if (!retryOriginal) selected.forEach(index => {
      const entry = suggestion.suggestions[index], fields = draft[index] || {};
      if (entry.new_name) newTags[String(index)] = { name: String(fields.name ?? entry.new_name).trim() || entry.new_name,
        kind: fields.kind || entry.kind,
        parent_ids: fields.parent_ids ?? entry.parent_ids ?? [], top_level: fields.top_level === true,
        ...(entry.retired_tag ? { levels: fields.levels || [] } : {}) };
    });
    const previous = suggestionAttempts.get(suggestion.id);
    const fields = retryOriginal && previous ? { action: previous.payload.action, indices: previous.payload.indices,
      expected_revision: previous.payload.expected_revision, new_tags: previous.payload.new_tags }
      : { action, indices: selected, expected_revision: item.tag_revision, new_tags: newTags };
    const signature = JSON.stringify(fields);
    if (previous && previous.signature !== signature) { toast("上次提交结果尚未核实，请先按原操作重试；核实后再修改。", "error"); return; }
    const attempt = previous || { itemId: item.id, suggestionId: suggestion.id, signature, payload: { ...fields, request_id: tagRequestId() } };
    suggestionAttempts.set(suggestion.id, attempt); suggestionLocks.add(suggestion.id);
    saveSuggestionDrafts();
    panel.setAttribute("aria-busy", "true");
    const controls = Array.from(panel.querySelectorAll("button, input, select"));
    controls.forEach(control => { control.dataset.wasDisabled = String(control.disabled); control.disabled = true; });
    try {
      const result = await tagRequest("/api/library/tag-suggestions/" + suggestion.id + "/resolve", { method: "POST", body: JSON.stringify(attempt.payload) });
      suggestionAttempts.delete(suggestion.id); attempt.payload.indices.forEach(index => delete draft[index]); saveSuggestionDrafts();
      toast((result.message || "上次提交已核实") + "。", "success"); await refreshTaggedPublications([item.id]);
    } catch (error) {
      if (error.status && error.status < 500) suggestionAttempts.delete(suggestion.id);
      saveSuggestionDrafts();
      if (error.stale) {
        await refreshTaggedPublications([item.id]);
        toast(error.message + " 旧建议已移入 AI 标签记录；正式标签未改变。", "error");
      } else toast(error.message + " 已保留输入；重试会核实原请求，避免重复采纳。", "error");
    } finally {
      suggestionLocks.delete(suggestion.id); panel.removeAttribute("aria-busy");
      controls.forEach(control => { control.disabled = control.dataset.wasDisabled === "true"; delete control.dataset.wasDisabled; });
      panel.dispatchEvent(new Event("suggestion-unlocked"));
      inspectorRenderedKey = null; renderInspector(state.catalog.get(state.inspectorId));
      if (tagDialog?.open && tagTargetIds.length === 1 && tagTargetIds[0] === item.id) renderTagPickerNow();
    }
  }
  async function openTagSuggestionHistory(item) {
    let dialog = node("dialog", "tag-history-dialog");
    const heading = node("header", "tag-picker-head"), title = node("h2", "", "AI 标签记录 · 第 " + item.number + " 题");
    const close = node("button", "button", "关闭"); close.type = "button"; heading.append(title, close);
    const list = node("div", "tag-history-list"), footer = node("footer", "tag-picker-foot"), status = node("span", "helper"), previous = node("button", "button button-small", "较新记录"), next = node("button", "button button-small", "更早记录");
    previous.type = next.type = "button"; footer.append(status, previous, next); dialog.append(heading, list, footer); document.body.append(dialog);
    let offset = 0, total = 0, loading = false;
    const loadPage = async function() {
      if (loading) return; loading = true; list.replaceChildren(node("p", "helper", "正在读取记录…"));
      try {
        const url = "/api/library/tags/suggestions?publication_id=" + encodeURIComponent(item.id) + "&status=all&limit=20&offset=" + offset;
        const result = await tagRequest(url); total = result.total || 0; list.replaceChildren();
        const statusNames = { queued: "排队中", running: "生成中", pending: "待核对", accepted: "已采纳", rejected: "已拒绝", no_match: "无合适标签", review: "暂不能判断", failed: "生成失败", stale: "依据已变化" };
        (result.items || []).forEach(function(record) {
          const article = node("article", "tag-history-record"), top = node("header"), stateText = statusNames[record.status] || record.status;
          top.append(node("strong", "", stateText), node("small", "", new Date(record.created_at).toLocaleString() + (record.generation_source ? " · " + record.generation_source : "")));
          article.append(top);
          if (record.message) article.append(node("p", "", record.message));
          const snapshot = record.catalogue_snapshot || {}, snapshotRows = snapshot.tags || [];
          (record.suggestions || []).forEach(function(entry) {
            const line = node("div", "tag-history-entry"), decision = entry.decision ? (entry.decision === "accepted" ? " · 已采纳" : " · 已拒绝") : " · 未处理";
            const original = snapshotRows.find(tag => tag.id === entry.tag_id);
            line.append(node("b", "", original?.name || entry.name || entry.new_name || "新标签建议"), node("span", "", decision));
            const paths = original ? tagAncestorPaths(original.id, 6, snapshotRows).paths
              : (entry.parent_ids || []).flatMap(id => tagAncestorPaths(id, 6, snapshotRows).paths.map(path => [...path, { name: entry.new_name || entry.name || "新标签" }]));
            if (paths.length) {
              const location = node("details", "tag-history-directory"); location.append(node("summary", "", "生成时的归属路径"));
              paths.forEach(path => location.append(node("p", "", path.map(tag => tag.name).join(" › ")))); line.append(location);
            }
            if (entry.rationale) line.append(node("p", "", entry.rationale));
            if (entry.evidence) line.append(node("small", "", "依据：" + entry.evidence));
            article.append(line);
          });
          if (snapshotRows.length) {
            const directory = node("details", "tag-history-directory");
            directory.append(node("summary", "", `生成时的标签目录 · 版本 ${snapshot.revision ?? "未知"}`));
            snapshotRows.forEach(tag => {
              const row = node("p", ""), parents = (tag.parent_ids || []).map(id => snapshotRows.find(value => value.id === id)?.name).filter(Boolean);
              row.append(node("b", "", tag.name), node("small", "", parents.length ? "上级：" + parents.join("、") : "顶级标签")); directory.append(row);
            }); article.append(directory);
          }
          list.append(article);
        });
        if (!result.items?.length) list.append(node("p", "library-empty", "还没有 AI 标签记录。"));
        status.textContent = total ? `${offset + 1}–${Math.min(offset + (result.items || []).length, total)} / ${total} 条` : "0 条记录";
        previous.disabled = offset <= 0; next.disabled = offset + 20 >= total;
      } catch (error) { list.replaceChildren(node("p", "helper", error.message)); }
      finally { loading = false; }
    };
    previous.addEventListener("click", function() { offset = Math.max(0, offset - 20); void loadPage(); });
    next.addEventListener("click", function() { if (offset + 20 < total) { offset += 20; void loadPage(); } });
    close.addEventListener("click", function() { dialog.close(); });
    dialog.addEventListener("close", function() { dialog.remove(); }); dialog.showModal(); void loadPage();
  }
  function tagSuggestionNode(item, { embedded = false } = {}) {
    const suggestion = item.tag_suggestion;
    if (!suggestion || !["pending", "queued", "running", "review", "no_match", "failed", "stale", "accepted", "rejected"].includes(suggestion.status)) return null;
    if (["accepted", "rejected"].includes(suggestion.status)) return null;
    if (suggestion.status !== "pending") {
      const compact = node("div", "tag-suggestion-compact");
      const label = { queued: "AI 标签建议排队中", running: "AI 正在生成标签建议", review: "AI 暂不能判断", no_match: "AI 没有找到合适标签", failed: "AI 标签建议生成失败", stale: "AI 建议依据已变化" }[suggestion.status];
      compact.append(node("span", "", label));
      if (suggestion.message) compact.append(node("small", "", suggestion.message));
      if (suggestion.status === "failed") {
        const retry = node("button", "button button-small tag-suggestion-retry-inline", "重新建议");
        retry.type = "button"; retry.addEventListener("click", () => queueJobs("tags", { ids: [item.id], retry: true }));
        compact.append(retry);
      }
      return compact;
    }
    const draft = suggestionDraft(item, suggestion), busy = suggestionLocks.has(suggestion.id);
    const panel = node("aside", "tag-suggestion-card");
    panel.dataset.status = suggestion.status;
    panel.setAttribute("aria-label", "AI 标签建议");
    const heading = node("header", "tag-suggestion-heading");
    const statusText = { pending: "待核对", queued: "排队中", running: "生成中", review: "暂不能判断", no_match: "无合适标签", failed: "生成失败", stale: "依据已变化", accepted: "已采纳", rejected: "已拒绝" }[suggestion.status];
    heading.append(node("h3", "", "AI 标签建议"), node("span", "tag-suggestion-status", statusText));
    // Inside the merged dialog the block above already says "AI 建议的"; repeating it
    // here would spend a whole header row on the same four characters.
    if (embedded) panel.append(node("p", "tag-suggestion-message", statusText));
    else panel.append(heading);
    if (suggestion.message) panel.append(node("p", "tag-suggestion-message", suggestion.message));
    const checks = [];
    (suggestion.suggestions || []).forEach((entry, index) => {
      if (entry.decision) return;
      const block = node("div", "tag-suggestion-entry");
      block.dataset.suggestionIndex = String(index);
      const entryDraft = draft[index] ||= {};
      const line = node("label", "tag-suggestion-line");
      const check = node("input"); check.type = "checkbox"; check.value = String(index); check.dataset.tagSuggestion = suggestion.id; check.disabled = false;
      check.checked = entryDraft.selected === true;
      check.addEventListener("change", () => { entryDraft.selected = check.checked; saveSuggestionDrafts(); });
      const label = entry.name || entry.new_name || "新标签建议";
      const scope = entry.kind === "method" && !entry.scope?.whole ? "第 " + ((entry.scope?.subquestions || []).join("、") || "?") + " 问" : "整题";
      const text = node("span", "tag-suggestion-label");
      text.append(node("b", "tag-suggestion-name", label));
      const meta = node("span", "tag-suggestion-meta");
      meta.append(node("span", "", entry.kind === "method" ? "解题方法" : "知识点"), node("span", "", scope));
      if (entry.new_name) meta.append(node("span", "tag-suggestion-new", entry.retired_tag ? "旧标签" : "新标签"));
      text.append(meta); line.append(check, text); block.append(line); checks.push(check);
      if (entry.retired_tag) {
        // 这条以前会被当成全新标签，于是要你从一堆标签里挑个上级；
        // 实际上它一直躺在你的库里，只是当年没启用。说出来，才不用你做那个选择。
        const known = node("p", "tag-suggestion-rationale");
        known.append(node("b", "", "你的库里已经有「" + entry.retired_tag.name + "」了"),
          document.createTextNode("，只是以前没启用，AI 才当成了新标签。采纳就会把它重新启用；点「修改名称或上级」仍可以改别的。"));
        block.append(known);
        const levels = node("fieldset", "tag-suggestion-groups");
        levels.append(node("legend", "", "重新启用后适用哪一学段（必须选）"));
        const chosen = entryDraft.levels ?? [];
        ["初中", "高中"].forEach(function(level) {
          const row = node("label", "tag-suggestion-group-list"), box = node("input");
          box.type = "checkbox"; box.value = level; box.checked = chosen.includes(level);
          box.dataset.retiredLevel = suggestion.id + "-" + index;
          box.addEventListener("change", () => {
            entryDraft.levels = Array.from(levels.querySelectorAll("input:checked")).map(input => input.value);
            saveSuggestionDrafts();
          });
          row.append(box, node("span", "", level)); levels.append(row);
        });
        block.append(levels);
      }
      if (entry.rationale) block.append(node("p", "tag-suggestion-rationale", entry.rationale));
      if (entry.evidence) {
        const evidence = node("details", "tag-suggestion-evidence");
        evidence.open = entryDraft.evidence === true;
        evidence.addEventListener("toggle", () => { entryDraft.evidence = evidence.open; saveSuggestionDrafts(); });
        evidence.append(node("summary", "", "查看依据"), node("p", "", entry.evidence)); block.append(evidence);
      }
      if (entry.new_name) {
        const edit = node("details", "tag-suggestion-edit"); edit.append(node("summary", "", "修改名称或上级"));
        edit.open = entryDraft.expanded === true;
        const fields = node("div", "tag-suggestion-fields");
        const nameLabel = node("label", "tag-suggestion-field"), input = node("input", "tag-suggestion-new-name");
        input.value = entryDraft.name ?? entry.new_name; input.dataset.newTagName = suggestion.id + "-" + index;
        input.addEventListener("input", () => { entryDraft.name = input.value; saveSuggestionDrafts(); });
        nameLabel.append(node("span", "", "标签名称"), input);
        const kindLabel = node("label", "tag-suggestion-field"), kind = node("select");
        kind.dataset.newTagKind = suggestion.id + "-" + index;
        [["knowledge", "知识点"], ["method", "解题方法"]].forEach(function(option) { kind.add(new Option(option[1], option[0])); }); kind.value = entryDraft.kind || entry.kind;
        kind.addEventListener("change", () => { entryDraft.kind = kind.value; entryDraft.parent_ids = []; renderParents(); saveSuggestionDrafts(); });
        kindLabel.append(node("span", "", "标签类型"), kind);
        const parentLabel = node("label", "tag-suggestion-field"), parents = node("select", "tag-suggestion-parents");
        parents.multiple = true; parents.size = 4; parents.dataset.newTagParent = suggestion.id + "-" + index;
        parentLabel.append(node("span", "", "上级标签（可多选）"), parents);
        const topLabel = node("label", "tag-suggestion-top-level"), top = node("input"); top.type = "checkbox";
        top.checked = entryDraft.top_level === true; topLabel.append(top, node("span", "", "作为顶级标签"));
        const renderParents = () => {
          parents.replaceChildren();
          (tagCatalogue.tags || []).filter(tag => tag.active && tag.kind === kind.value).forEach(tag => {
            const option = new Option(tag.name, tag.id); option.selected = (entryDraft.parent_ids ?? entry.parent_ids ?? []).includes(tag.id); parents.append(option);
          }); parents.disabled = top.checked || suggestionLocks.has(suggestion.id);
        };
        parents.addEventListener("change", () => { entryDraft.parent_ids = Array.from(parents.selectedOptions).map(option => option.value); entryDraft.top_level = false; top.checked = false; saveSuggestionDrafts(); });
        top.addEventListener("change", () => {
          entryDraft.top_level = top.checked; if (top.checked) entryDraft.parent_ids = [];
          renderParents(); saveSuggestionDrafts();
        }); renderParents();
        let loaded = false;
        const ensureCatalogue = async function() {
          if (loaded || !edit.open) return;
          try { await loadTagCatalogue(); renderParents(); loaded = true; }
          catch (error) { const retry = node("button", "button button-small", "重试载入标签"); retry.type = "button";
            retry.addEventListener("click", ensureCatalogue); top.after(retry); }
        };
        edit.addEventListener("toggle", () => { entryDraft.expanded = edit.open; saveSuggestionDrafts(); void ensureCatalogue(); });
        fields.append(nameLabel, kindLabel, parentLabel, topLabel); edit.append(fields); block.append(edit);
      }
      const entryActions = node("div", "tag-suggestion-entry-actions"), acceptOne = node("button", "button button-small tag-suggestion-accept", "采纳"), rejectOne = node("button", "button button-quiet button-small tag-suggestion-reject", "拒绝");
      acceptOne.type = rejectOne.type = "button";
      acceptOne.addEventListener("click", () => resolveTagSuggestion(item, "accept", panel, [index]));
      rejectOne.addEventListener("click", () => resolveTagSuggestion(item, "reject", panel, [index]));
      entryActions.append(acceptOne, rejectOne); block.append(entryActions);
      panel.append(block);
    });
    if (!checks.length) return null;
    if (suggestion.status === "pending") {
      const actions = node("footer", "tag-suggestion-actions"), selection = node("p", "tag-suggestion-selection", "勾选要处理的建议");
      const buttons = node("div", "tag-suggestion-buttons");
      const accept = node("button", "button button-small button-primary", "采纳所选建议"); accept.type = "button"; accept.addEventListener("click", () => resolveTagSuggestion(item, "accept", panel));
      const reject = node("button", "button button-small", "拒绝所选建议"); reject.type = "button"; reject.addEventListener("click", () => resolveTagSuggestion(item, "reject", panel));
      const updateSelection = function() {
        const count = checks.filter(function(check) { return check.checked && !check.disabled; }).length;
        selection.textContent = count ? "已选 " + count + " 条建议" : "勾选要处理的建议";
        accept.disabled = reject.disabled = count === 0;
      };
      checks.forEach(function(check) { check.addEventListener("change", updateSelection); }); updateSelection();
      panel.addEventListener("suggestion-unlocked", updateSelection);
      buttons.append(accept, reject); actions.append(selection, buttons); panel.append(actions);
    }
    if (busy) { panel.setAttribute("aria-busy", "true"); panel.querySelectorAll("button, input, select").forEach(control => { control.disabled = true; }); }
    return panel;
  }
  function cardTagContext(item) {
    const context = node("div", "library-card-context");
    (item.tag_assignments || []).filter(link => !link.needs_review && link.tag.kind === "knowledge").slice(0, 2)
      .forEach(link => context.append(tagHoverChip(item, link, "library-card-tag-glimpse")));
    const pendingCount = item.tag_suggestion?.status === "pending" ? (item.tag_suggestion.suggestions || []).filter(entry => !entry.decision).length : 0;
    if (pendingCount) context.append(node("span", "library-card-ai-glimpse", `AI 建议 ${pendingCount} 条待核对`));
    if (state.retainedTagItems.has(item.id)) context.append(node("span", "library-tag-retained", "已不符合当前筛选，暂时保留；重新筛选后移出列表"));
    return context;
  }
  function updateLibraryMatchStatus() {
    if (state.view === "selected") return;
    const retained = state.retainedTagItems.size;
    setStatus(`已显示 ${state.items.length} / 共 ${state.total} 题${retained ? ` · 另保留 ${retained} 题供继续阅读` : ""} · 每题保留入库版本。`);
    ui.more.hidden = state.items.length >= state.total;
  }
  function patchTagPresentation(ids) {
    const viewport = captureLibraryViewport();
    for (const id of ids) {
      const item = state.catalog.get(id), article = document.getElementById(`q-${id}`);
      if (!item || !article) continue;
      const context = cardTagContext(item), old = article.querySelector(".library-card-context");
      if (old) old.replaceWith(context); else article.querySelector(".library-card-body")?.append(context);
      article.classList.toggle("tag-filter-retained", state.retainedTagItems.has(id));
      const kept = state.cards?.get(id);
      if (kept) kept.signature = JSON.stringify([item, state.basket.includes(id), state.features, state.ai.mode, state.selected.has(id), state.expanded.has(id), state.opened.has(id), state.tag, state.tagAll, state.tagAny, state.tagNot, state.tagMode]);
    }
    if (ids.includes(state.inspectorId)) { inspectorRenderedKey = null; renderInspector(state.catalog.get(state.inspectorId)); }
    updateLibraryMatchStatus();
    restoreLibraryViewport(viewport);
    positionTagHover();
  }
  function applyInlineTagResult(item, result) {
    if (result.revisions?.[item.id] !== undefined) item.tag_revision = result.revisions[item.id];
    if (Array.isArray(result.assignments) && (!result.publication_id || result.publication_id === item.id)) {
      item.tag_assignments = result.assignments;
      item.tags = result.assignments.filter(link => !link.needs_review).map(link => link.tag.name);
    }
  }
  // Only the tag fields are refreshed. The question body, answer disclosure,
  // selection and currently inspected identity keep their existing DOM/state.
  async function refreshTaggedPublications(ids, result = null) {
    ids = Array.from(new Set(ids));
    const positions = new Map(visibleItems().map((item, index) => [item.id, index]));
    const filters = currentFilterPayload(), filterKey = JSON.stringify(filters), view = state.view;
    if (result) ids.forEach(id => { const item = state.catalog.get(id); if (item) applyInlineTagResult(item, result); });
    const body = await tagRequest("/api/library/batch", { method: "POST", body: JSON.stringify({ ids, ...(view === "all" ? { filters } : {}) }) });
    for (const fresh of body.items || []) {
      const item = state.catalog.get(fresh.id) || fresh;
      for (const key of ["tag_revision", "tag_assignments", "tags", "tag_suggestion", "job_errors", "job_details", "jobs"]) {
        if (key in fresh) item[key] = fresh[key];
      }
      state.catalog.set(item.id, item);
      if (view === "all" && state.view === view && JSON.stringify(currentFilterPayload()) === filterKey && body.matches) {
        const index = positions.get(item.id);
        if (body.matches[item.id] === false && index !== undefined) {
          state.retainedTagItems.set(item.id, { item, index });
          state.items = state.items.filter(value => value.id !== item.id);
        } else if (body.matches[item.id] === true && state.retainedTagItems.has(item.id)) {
          const retained = state.retainedTagItems.get(item.id); state.retainedTagItems.delete(item.id);
          if (!state.items.some(value => value.id === item.id)) state.items.splice(Math.min(retained.index, state.items.length), 0, item);
        }
      }
    }
    if (view === "all" && state.view === view && JSON.stringify(currentFilterPayload()) === filterKey && Number.isInteger(body.total)) {
      state.total = body.total; state.facets = body.facets || state.facets; state.tagWork = body.tag_work || state.tagWork;
      renderFacets(); renderActiveFilters();
    }
    patchTagPresentation(ids);
    return body;
  }
  async function submitInlineTagAttempt(item, attempt) {
    if (inlineTagLocks.has(item.id)) return false;
    inlineTagLocks.add(item.id); inlineTagStates.set(item.id, { attempt, message: attempt.uncertain ? "正在核实上次操作…" : "正在保存标签…" });
    patchTagPresentation([item.id]); renderTagHover();
    try {
      let result = null;
      if (attempt.uncertain) {
        const history = await tagRequest("/api/library/tags/operations?request_id=" + encodeURIComponent(attempt.payload.request_id));
        const prior = (history.operations || []).find(operation => operation.request_id === attempt.payload.request_id);
        if (prior) result = { operation_id: prior.id, revisions: prior.revisions || prior.after_snapshot?.revisions, undone: prior.undone };
      }
      if (!result) result = await tagRequest("/api/library/tags/assignments/batch", { method: "POST", body: JSON.stringify(attempt.payload) });
      inlineTagStates.set(item.id, { ...(result.undone ? {} : { operationId: result.operation_id, undoSourceTagId: attempt.payload.replace?.from_tag_id }), message: result.undone ? "上次操作已撤销" : attempt.message });
      applyInlineTagResult(item, result);
      if (tagHover?.publicationId === item.id && attempt.payload.replace && !result.undone) {
        tagHover.sourceTagId = attempt.payload.replace.to_tag_id; tagHover.viewTagId = tagHover.sourceTagId;
        tagHover.up.clear(); tagHover.down.clear();
      }
      try { await refreshTaggedPublications([item.id], result); }
      catch (_) { patchTagPresentation([item.id]); toast("标签已保存，筛选统计尚未更新；请稍后刷新。", "error"); }
      return true;
    } catch (error) {
      const uncertain = !error.status || error.status >= 500; attempt.uncertain = uncertain;
      inlineTagStates.set(item.id, { ...(uncertain ? { attempt } : {}), message: error.message });
      toast(error.message + (uncertain ? " 请核实原操作，避免重复保存。" : " 本题标签已保留。"), "error");
      return false;
    } finally { inlineTagLocks.delete(item.id); patchTagPresentation([item.id]); renderTagHover(); }
  }
  async function removeInlineTag(item, tag) {
    item = state.catalog.get(item.id) || item;
    const prior = inlineTagStates.get(item.id);
    if (prior?.attempt) { toast("先核实上次标签操作，再修改其他标签。", "error"); return; }
    return submitInlineTagAttempt(item, { tagId: tag.id, tag, message: `已移除「${tag.name}」`, payload: {
      publication_ids: [item.id], add: [], remove: [tag.id], scopes: {},
      expected_revisions: { [item.id]: item.tag_revision }, request_id: tagRequestId() } });
  }
  async function undoInlineTag(item) {
    const saved = inlineTagStates.get(item.id); if (!saved?.operationId || inlineTagLocks.has(item.id)) return;
    inlineTagLocks.add(item.id); inspectorRenderedKey = null; renderInspector(state.catalog.get(state.inspectorId));
    try { const result = await tagRequest("/api/library/tags/undo", { method: "POST", body: JSON.stringify({ operation_id: saved.operationId }) });
      if (tagHover?.publicationId === item.id && saved.undoSourceTagId) {
        tagHover.sourceTagId = saved.undoSourceTagId; tagHover.viewTagId = saved.undoSourceTagId; tagHover.up.clear(); tagHover.down.clear();
      }
      inlineTagStates.set(item.id, { message: "已撤销标签修改" }); await refreshTaggedPublications([item.id], result);
    } catch (error) { toast(error.message + " 后续改动已保留。", "error"); }
    finally { inlineTagLocks.delete(item.id); patchTagPresentation([item.id]); renderTagHover(); }
  }
  function extrasNode(item, { includeSuggestions = true } = {}) {
    const box = node("div", "library-extras");
    const row = node("div", "library-tags");
    (item.tag_assignments || []).filter((link) => !link.needs_review).forEach((link) => {
      const tag = link.tag;
      const wrapper = node("div", "library-tag-chip"), chip = tagHoverChip(item, link, "library-tag");
      if (tag.color) wrapper.style.setProperty("--tag-color", tag.color);
      const remove = node("button", "library-tags-remove", "×"); remove.type = "button"; remove.dataset.tagId = tag.id; remove.title = `从本题移除「${tag.name}」`; remove.setAttribute("aria-label", remove.title);
      remove.disabled = inlineTagLocks.has(item.id); remove.addEventListener("click", () => removeInlineTag(item, tag)); wrapper.append(chip, remove); row.append(wrapper);
    });
    const edit = node("button", "library-tags-edit", "＋ 添加标签"); edit.type = "button";
    edit.title = "为当前这道题添加标签；AI 的建议就在这个按钮下面，可以直接改名、采纳或拒绝"; edit.disabled = inlineTagLocks.has(item.id);
    edit.addEventListener("click", () => openTagEditor(item)); row.append(edit); box.append(row);
    const saved = inlineTagStates.get(item.id);
    if (saved) {
      const status = node("div", "library-tags-save-state"); status.setAttribute("role", "status"); status.append(node("small", "", saved.message));
      if (saved.operationId) { const undo = node("button", "button button-quiet button-small library-tags-undo", "撤销"); undo.type = "button"; undo.disabled = inlineTagLocks.has(item.id); undo.addEventListener("click", () => undoInlineTag(item)); status.append(undo); }
      if (saved.attempt) { const retry = node("button", "button button-small library-tags-retry", "核实上次操作"); retry.type = "button"; retry.disabled = inlineTagLocks.has(item.id); retry.addEventListener("click", () => submitInlineTagAttempt(state.catalog.get(item.id) || item, saved.attempt)); status.append(retry); }
      box.append(status);
    }
    const unresolved = (item.tag_assignments || []).filter((link) => link.needs_review);
    if (unresolved.length) {
      const review = node("div", "library-tag-legacy-review");
      review.append(node("span", "helper", "旧标签待核对：" + unresolved.map((link) => link.tag.name).join("、")));
      const manage = node("button", "button button-quiet button-small", "整理旧标签"); manage.type = "button";
      manage.addEventListener("click", openTagManager); review.append(manage); box.append(review);
    }
    if (includeSuggestions) {
      const pending = Array.from(suggestionAttempts.values()).find(attempt => attempt.itemId === item.id);
      if (pending && !suggestionLocks.has(pending.suggestionId)) {
        const recovery = node("div", "tag-suggestion-recovery"); recovery.append(node("small", "", "上次 AI 建议处理结果尚未核实"));
        const retry = node("button", "button button-small tag-suggestion-retry", "核实上次提交"); retry.type = "button";
        retry.addEventListener("click", () => resolveTagSuggestion({ ...item, tag_suggestion: { id: pending.suggestionId, status: "pending", suggestions: [] } }, pending.payload.action, recovery, pending.payload.indices, true)); recovery.append(retry); box.append(recovery);
      }
      // 待核对的建议直接摊在这里，而不是先摆一个「去核对」的按钮。
      // 这块下面本来就是空着的，而核对是最高频的动作 —— 让人点一下才看得见
      // AI 到底建议了什么，等于把最该显眼的东西藏进另一个窗口里。
      // 改名、采纳、拒绝都在这儿做完，标签窗口不再重复摆一套。
      if (item.tag_suggestion?.status === "pending") {
        const pendingCount = (item.tag_suggestion.suggestions || []).filter(entry => !entry.decision).length;
        const panel = tagSuggestionNode(item, { embedded: true });
        if (panel) box.append(panel);
        else {
          // 一条未定的建议都画不出来时（都已处理过），只说明「剩下的去哪儿看」，
          // 不再重复摆一套采纳/拒绝。
          const summary = node("div", "tag-suggestion-summary");
          const open = node("button", "button button-small tag-suggestion-open",
            pendingCount ? `AI 有 ${pendingCount} 条建议待核对 · 去核对` : "AI 的建议已核对完 · 打开标签窗口");
          open.type = "button"; open.title = "打开标签窗口添加标签";
          open.addEventListener("click", () => openTagEditor(item)); summary.append(open);
          if (item.tag_suggestion.message) summary.append(node("small", "", item.tag_suggestion.message));
          box.append(summary);
        }
      } else {
        const panel = tagSuggestionNode(item); if (panel) box.append(panel);
      }
    }
    if (item.tag_suggestion) {
      const history = node("button", "button button-quiet button-small library-tag-history-link", "AI 标签记录"); history.type = "button";
      history.addEventListener("click", () => openTagSuggestionHistory(item)); box.append(history);
    }
    Object.entries(item.job_errors || {})
      .filter(([kind, message]) => (kind === "tags" ? true : state.features.ai_answer)
        && !(kind === "tags" && item.tag_suggestion?.status === "failed" && item.tag_suggestion.message === message))
      .forEach(([kind, message]) => {
        const words = describeJobFailure(kind, message, aiReady(kind));
        const error = node("p", "library-job-error");
        error.title = words.title;
        error.append(node("strong", "", words.head));
        error.append(node("span", "", words.body));
        if (words.body2) error.append(node("span", "", words.body2));
        box.append(error);
      });
    return box.childNodes.length ? box : null;
  }

  // 「这个功能现在能不能用」只有一个口径。按钮显不显示、失败提示说不说人话，
  // 都从这里判断；写两份判定迟早会有一处忘了跟着改，于是界面又开始骗人。
  function aiReady(kind) {
    if (state.ai.mode === "assistant") return kind === "tags" && state.ai.ready === true;
    return state.ai.mode === "api" && state.ai.api_ready === true;
  }

  // 任务失败提示该怎么说，是另一件要单独定下来的事。同一条历史原因，在
  // 「服务还没配好」和「后来配好了」两种时候该讲的话完全不同：照抄历史原因，
  // 会让人以为此刻还没配好，于是反复回头检查已经配好的设置。
  // ready 传的是此刻的真实状态，不是当时的状态。
  function describeJobFailure(kind, message, ready) {
    const label = kind === "tags" ? "AI 建议标签" : "AI 生成答案";
    const configurationIssue = /API 配置|服务商配置|密钥配置|模型配置|请重新配置|尚未配置/.test(message);
    if (configurationIssue && ready) {
      return {
        head: `${label}上次没跑成`,
        body: "，当时服务还没配好。现在已经配好了——整批落下的活，回题库页点「"
          + (kind === "tags" ? "补齐缺的知识点标签" : "补齐缺的参考答案") + "」一起补就行。",
        title: `这道题上次${kind === "tags" ? "生成标签建议" : "生成参考答案"}时，服务还没配好，所以没跑成。现在服务已经配好，可以重来。`
      };
    }
    return {
      head: `${label}未完成`,
      body: `：${message}`,
      body2: configurationIssue ? " 请到“设置 → API 配置”里配好并测试。" : "",
      title: message
    };
  }

  function jobButtons(item) {
    const buttons = [];
    const tagReady = aiReady("tags");
    const answerReady = aiReady("answer");
    if (!tagReady && !answerReady) return buttons;
    const details = item.job_details || [];
    const waiting = new Set(details.length ? details.filter((job) => job.executor === state.ai.mode).map((job) => job.kind) : item.jobs || []);
    if (tagReady && state.features.knowledge_tags && (!item.tag_suggestion || ["failed", "no_match", "review", "accepted", "rejected", "stale"].includes(item.tag_suggestion.status))) {
      const busy = waiting.has("tags");
      const prior = item.tag_suggestion && ["failed", "no_match", "review", "stale", "accepted", "rejected"].includes(item.tag_suggestion.status);
      const button = iconButton("button", "button button-quiet button-small", busy ? "正在生成标签建议…" : prior ? "重新建议" : state.ai.mode === "assistant" ? "交给助手建议标签" : "AI 建议标签", busy ? "" : "plus");
      button.disabled = busy;
      button.addEventListener("click", () => queueJobs("tags", { ids: [item.id], retry: Boolean(prior) }));
      buttons.push(button);
    }
    if (answerReady && state.features.ai_answer && !item.has_answer && !item.ai_answer) {
      const busy = waiting.has("answer");
      const assistant = state.ai.mode === "assistant" && details.some((job) => job.kind === "answer" && job.executor === "assistant");
      const button = iconButton("button", "button button-quiet button-small", busy ? (assistant ? "待助手解答" : "AI 正在解答…") : "AI 解答", busy ? "" : "plus");
      button.disabled = busy;
      button.title = "原卷没有答案：使用已配置的 API 生成，结果标着“AI 参考 · 未核对”";
      button.addEventListener("click", () => queueJobs("answer", { ids: [item.id] }));
      buttons.push(button);
    }
    return buttons;
  }

  function render() {
    const viewport = captureLibraryViewport();
    renderFacets();
    const notice = $("assistantTaskNotice");
    notice.hidden = true;
    notice.textContent = "";
    renderActiveFilters();
    renderBasket();
    syncSelection();
    ui.list.replaceChildren();
    const shown = visibleItems();
    if (!shown.some((item) => item.id === state.inspectorId)) state.inspectorId = shown[0]?.id || "";
    if (!shown.length) {
      const empty = node("div", "library-empty");
      const filtered = Boolean(state.q.trim() || state.document || state.type || state.review || state.answer || state.tag
        || state.tagAll || state.tagAny || state.tagNot || state.untaggedKnowledge || state.tagReview);
      empty.append(node("p", "", state.view === "selected" ? (state.basket.length ? "选题尚未载入。" : "试题篮还是空的。") : filtered ? "没有找到符合条件的题目。" : "题库还是空的。"));
      if (state.view === "selected") empty.append(node("p", "helper", state.basket.length ? "在右侧重试，或移出已撤回的题目。" : "回到全部题目，选择需要的题加入试题篮。"));
      else if (filtered) {
        empty.append(node("p", "helper", "试试其他关键词，或清除筛选查看全部题目。"));
        const reset = node("button", "button button-small", "清除搜索与筛选");
        reset.type = "button";
        reset.addEventListener("click", () => {
          clearFilters();
        });
        empty.append(reset);
      } else {
        empty.append(node("p", "helper", "先导入试卷，核对题卡后点“入库”，题目就会出现在这里。没有密钥也能从原卷选题。"));
        const upload = node("a", "button button-primary", "导入试卷");
        upload.href = "/#dropZone";
        empty.append(upload);
      }
      ui.list.append(empty);
    }
    // Unchanged cards are kept as they are (an opened answer stays open while jobs finish).
    const previous = state.cards || new Map();
    state.cards = new Map();
    shown.forEach((item) => {
      const signature = JSON.stringify([item, state.basket.includes(item.id), state.features, state.ai.mode, state.selected.has(item.id), state.expanded.has(item.id), state.opened.has(item.id), state.tag, state.tagAll, state.tagAny, state.tagNot, state.tagMode]);
      const kept = previous.get(item.id);
      const node = kept && kept.signature === signature ? kept.node : card(item);
      node.classList.toggle("inspector-active", state.inspectorId === item.id);
      if (state.inspectorId === item.id) node.setAttribute("aria-current", "true"); else node.removeAttribute("aria-current");
      state.cards.set(item.id, { signature, node });
      ui.list.append(node);
    });
    renderInspector(state.catalog.get(state.inspectorId) || shown.find((item) => item.id === state.inspectorId) || null);
    const currentQuestion = state.catalog.get(state.questionReturnId);
    if ($("questionDialog").open && currentQuestion && state.questionSignature !== questionSignature(currentQuestion)) openQuestion(currentQuestion);
    setStatus(state.view === "selected" ? `试题篮 ${state.basket.length} 题 · 已载入 ${shown.length} 题。已选题目不受搜索与筛选影响。`
      : state.total
      ? `共 ${state.total} 题 · 已显示 ${state.items.length} 题${state.q ? `，匹配“${state.q}”` : ""}`
      : "");
    ui.more.hidden = state.view === "selected" || state.items.length >= state.total;
    if (state.view !== "selected" && state.retainedTagItems.size) updateLibraryMatchStatus();
    requestAnimationFrame(() => readingOverflowHints(ui.list));
    if (state.focus) {
      const target = document.getElementById(`q-${state.focus}`);
      if (target) {
        requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "center" }));
        state.focus = "";
      }
    } else restoreLibraryViewport(viewport);
  }

  // Optional guidance is checked where it is produced, so a re-render, a
  // question switch or a new window cannot bring a dismissed hint back.
  function hintHidden(category) {
    return Boolean(window.QBShortcutHelp?.hintDismissed?.(category));
  }

  // 「已显示 X / 共 Y 题」这类日常信息，窄屏收得起来；但「上次读取的结果还没应用」
  // 「撤回失败」这类话不能跟着一起消失 —— 手机上出了问题，界面上一个字都没有。
  function setStatus(text, tone = "plain") {
    ui.status.textContent = text;
    ui.status.dataset.tone = tone;
  }

  function readingOverflowHints(container) {
    container.querySelectorAll(".reading-overflow-hint").forEach((hint) => hint.remove());
    // A genuinely wide formula always stays focusable and horizontally
    // scrollable; only the sentence explaining it is dismissible.
    const hintOff = hintHidden("reading-overflow");
    container.querySelectorAll(".qb-stem-body, .qb-option-body, .qb-analysis").forEach((field) => {
      if (!field.clientWidth || field.scrollWidth <= field.clientWidth + 2) return;
      field.tabIndex = 0;
      field.setAttribute("aria-label", "题目内容，可左右滚动查看完整公式");
      if (hintOff) return;
      const hint = node("span", "reading-overflow-hint", "左右滑动查看完整公式；键盘可用左右方向键。");
      (field.closest(".qb-stem, .qb-option") || field).after(hint);
    });
  }

  function visibleItems() {
    if (state.view !== "selected") {
      const items = state.items.slice();
      for (const retained of state.retainedTagItems.values()) {
        if (!items.some(item => item.id === retained.item.id)) items.splice(Math.min(retained.index, items.length), 0, retained.item);
      }
      return items;
    }
    const missing = new Set(state.basketMissing.map((item) => item.id));
    return state.basket.filter((id) => !missing.has(id)).map((id) => state.catalog.get(id)).filter(Boolean);
  }

  function clearFilters() {
    window.clearTimeout(searchTimer);
    state.q = state.document = state.type = state.review = state.answer = state.tag = "";
    state.tagAll = state.tagAny = state.tagNot = ""; state.tagMode = "all"; state.untaggedKnowledge = state.tagReview = false;
    ui.search.value = "";
    syncUrl();
    void load();
  }

  function renderActiveFilters() {
    const box = $("activeFilters");
    box.replaceChildren();
    const source = state.facets?.sources?.find((row) => row.document_id === state.document);
    const labels = [["q", state.q.trim() ? `搜索：${state.q.trim()}` : ""],
      ["document", state.document ? `来源：${source?.filename || "当前试卷"}` : ""],
      ["type", state.type ? `题型：${QB.TYPE_NAMES[state.type] || state.type}` : ""],
      ["review", state.review ? (state.review === "ai" ? "AI 审核" : "人工核对") : ""],
      ["answer", state.answer ? (state.answer === "yes" ? "有原卷答案" : "无原卷答案") : ""],
      ["tag", state.tag ? `知识点：${state.tag}` : (state.tagAll || state.tagAny || state.tagNot) ? "标签筛选已启用" : state.untaggedKnowledge ? "未标知识点" : state.tagReview ? "AI 建议待核对" : ""]];
    box.hidden = state.view === "selected" || !labels.some(([, label]) => label);
    if (box.hidden) return;
    labels.forEach(([key, label]) => {
      if (!label) return;
      // 标签名和卷名都可能有二十来个字（目录里就有「分类加法计数原理与分步乘法计数原理」），
      // 212px 的筛选栏装不下。以前「标签 ×」是一整段文字，断行会断在词中间，
      // × 被挤到第二行单独待着，看着像出了错。改成名字截断、× 永远跟在同一行。
      const remove = node("button", "library-filter-chip");
      remove.type = "button";
      remove.title = label;
      remove.setAttribute("aria-label", `取消${label}`);
      remove.append(node("span", "label", label), node("span", "drop", "×"));
      remove.addEventListener("click", () => {
        if (key === "tag") { state.tag = ""; state.tagAll = state.tagAny = state.tagNot = ""; state.tagMode = "all"; state.untaggedKnowledge = state.tagReview = false; }
        else state[key] = "";
        ui.search.value = state.q; syncUrl(); void load();
      });
      box.append(remove);
    });
    const clear = node("button", "button button-quiet button-small", "清除筛选");
    clear.type = "button";
    clear.addEventListener("click", clearFilters);
    box.append(clear);
  }

  function syncSelection() {
    const shown = visibleItems();
    const count = shown.filter((item) => state.selected.has(item.id)).length;
    $("selectVisible").checked = Boolean(shown.length && count === shown.length);
    $("selectVisible").indeterminate = count > 0 && count < shown.length;
    $("selectVisible").disabled = !shown.length;
    $("selectionCount").textContent = `已勾选 ${state.selected.size} 题`;
    document.querySelector(".library-bulk")?.classList.toggle("has-selection", state.selected.size > 0);
    for (const [id, feature] of [["generateSelectedTags", "knowledge_tags"], ["generateSelectedAnswers", "ai_answer"]]) {
      const button = $(id);
      button.hidden = !state.features[feature] || state.ai.mode !== "api" || state.ai.api_ready !== true;
      button.disabled = !state.selected.size;
    }
    $("addSelected").disabled = !state.selected.size;
    $("withdrawSelected").disabled = !state.selected.size;
    $("clearSelection").disabled = !state.selected.size;
    $("editSelectedTags").disabled = !state.selected.size;
    $("editSelectedTags").textContent = state.selected.size ? `整理勾选题的标签（${state.selected.size}）` : "整理勾选题的标签";
  }

  async function batchItems(ids) {
    if (!ids.length) return { items: [], missing: [] };
    const response = await fetch("/api/library/batch", { method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: JSON.stringify({ ids }) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "选题读取失败，请重试");
    if (!Array.isArray(body.items) || !Array.isArray(body.missing)) throw new Error("选题返回不完整，请重试");
    const returned = new Set([...body.items, ...body.missing].map((item) => item.id));
    for (const id of ids) if (!returned.has(id)) body.missing.push({ id, reason: "题目未返回，请重试" });
    return body;
  }

  // 1.13.6：这道题为什么用不了。reason 是后端的状态枚举（superseded / withdrawn /
  // not_found），直接印出来就是一句英文。后端已经带了一句中文，这里以它为准，
  // 兜底再翻一次本地表 —— 界面上不该出现任何英文枚举。
  const MISSING_REASONS = { superseded: "题面改过，这道题已经有新版本了", withdrawn: "这道题已撤回", not_found: "这道题已不存在" };
  function missingReason(entry) {
    if (!entry) return "这道题当前不能使用";
    if (typeof entry === "string") return MISSING_REASONS[entry] || "这道题当前不能使用";
    return entry.message || MISSING_REASONS[entry.reason] || "这道题当前不能使用";
  }

  // 把篮里那一道换成它现在该用的版本，位置不变。老师刚改完题面回来发现的正是这个：
  // 篮里那道已经不能用了，可下一步该是什么，界面上一个字都没说。
  function swapBasketForReplacement(entry) {
    const replacement = entry?.replacement_id;
    if (!replacement || !state.basket.includes(entry.id)) return false;
    state.basket = state.basket.map((id) => (id === entry.id ? replacement : id));
    saveBasket();
    refreshBasket({ force: true });
    render();
    return true;
  }

  async function refreshBasket({ force = false } = {}) {
    const ids = [...state.basket];
    const signature = JSON.stringify(ids);
    if (!force && signature === state.basketSignature) return;
    state.basketSignature = signature;
    const token = ++state.basketToken;
    state.basketLoading = Boolean(ids.length);
    renderBasket();
    try {
      const body = await batchItems(ids);
      if (token !== state.basketToken) return;
      body.items.forEach((item) => state.catalog.set(item.id, item));
      state.basketMissing = body.missing;
      state.basketError = "";
    } catch (error) {
      if (token !== state.basketToken) return;
      state.basketError = error.message || "选题读取失败，请重试";
      state.basketSignature = null;
    } finally {
      if (token === state.basketToken) {
        state.basketLoading = false;
        renderBasket();
        if (state.view === "selected") render();
      }
    }
  }

  // 1.12.7：篮是右边缘一条常驻把手 + 一个抽屉面板，「组卷预览」就放在篮里。
  // 之前它在顶栏、侧栏、篮面板三处搬来搬去，用户找不到；现在位置固定、只差一次点击。
  function setBasketPanel(open) {
    const next = Boolean(open);
    if (next === state.basketVisible) return;
    if (next) setInspectorOpen(false);
    state.basketVisible = next;
    document.body.classList.toggle("library-basket-open", next);
    const handle = $("basketHandle");
    handle?.setAttribute("aria-expanded", String(next));
    const label = next ? "收起试题篮" : `展开试题篮（${state.basket.length} 题）`;
    if (handle) { handle.title = label; handle.setAttribute("aria-label", label); }
    $("basketPanel").hidden = !next;
    if (next) { setRail(false); if (!basketHandleSeen()) markBasketHandleSeen(); }
  }

  function renderBasket() {
    const panel = $("basketPanel");
    const handle = $("basketHandle");
    panel.hidden = !state.basketVisible;
    $("basketPanelCount").textContent = String(state.basket.length);
    $("basketHandleCount").textContent = String(state.basket.length);
    $("selectedViewCount").textContent = String(state.basket.length);
    $("clearBasketPanel").disabled = !state.basket.length;
    document.body.classList.toggle("library-basket-has-items", state.basket.length > 0);
    document.body.classList.toggle("library-basket-seen", basketHandleSeen());
    const label = state.basketVisible ? "收起试题篮" : `展开试题篮（${state.basket.length} 题）`;
    handle.setAttribute("aria-expanded", String(state.basketVisible));
    handle.title = label; handle.setAttribute("aria-label", label);
    ui.basketCount.textContent = String(state.basket.length);
    ui.basketButton.hidden = !state.basket.length;
    ui.basketButton.disabled = state.basketLoading;
    // 顶部不再常驻「全部题目 / 试题篮」这一对切换按钮：篮子由右边缘把手打开。
// 「只看篮里的题」是少数才需要的能力，只在篮子非空时露出；返回条只在篮里视图出现。
const selectedView = state.view === "selected";
const returnButton = $("allQuestionsButton"), enterButton = $("basketViewButton");
returnButton.hidden = !selectedView;
enterButton.hidden = selectedView || !state.basket.length;
enterButton.setAttribute("aria-pressed", String(selectedView));
$("selectedViewCount").textContent = String(state.basket.length);
    const counts = {}, sources = new Set();
    const list = $("basketList");
    list.replaceChildren();
    // 整条留着，不要只留一个 reason：换版本要用的 replacement_id 也在里面。
    const missing = new Map(state.basketMissing.map((item) => [item.id, item]));
    state.basket.forEach((id, index) => {
      const item = state.catalog.get(id);
      if (item && !missing.has(id)) {
        counts[item.question_type] = (counts[item.question_type] || 0) + 1;
        sources.add(item.document_id || item.source_filename);
      }
      const row = node("li", "basket-row");
      const open = node("button", "basket-item-open");
      open.type = "button";
      open.disabled = !item || missing.has(id);
      const name = item ? `${item.source_filename} · ${sourceLocator(item)}` : `选题 ${index + 1}`;
      open.append(node("strong", "", item ? `${sourceLocator(item)} · ${QB.TYPE_NAMES[item.question_type] || "其他"}` : `选题 ${index + 1}`), node("span", "", name));
      // 1.13.6：这里原来直接印 reason，那是后端的状态枚举。老师看到的是
      // 「第 1 题: superseded」—— 一句英文，看不出自己该怎么办。
      if (missing.has(id)) open.append(node("span", "basket-item-error", missingReason(missing.get(id))));
      open.addEventListener("click", () => openQuestion(item));
      const remove = node("button", "button button-quiet button-small basket-remove", "移出");
      remove.type = "button";
      remove.setAttribute("aria-label", `移出${name}`);
      remove.addEventListener("click", () => { state.basket = state.basket.filter((value) => value !== id); saveBasket(); render(); });
      if (missing.get(id)?.replacement_id) {
        // 题面改过以后篮里那道就废了，而老师多半正是刚改完题面回来接着组卷。
        // 只给「移出」的话，下一步该做什么全靠他自己猜。
        const swap = node("button", "button button-quiet button-small basket-remove", "换新版本");
        swap.type = "button";
        swap.setAttribute("aria-label", `${name} 换成当前版本`);
        swap.addEventListener("click", () => { swapBasketForReplacement(missing.get(id)); render(); });
        row.append(open, swap);
      } else {
        row.append(open);
      }
      row.append(remove);
      list.append(row);
    });
    $("basketSummary").textContent = state.basketLoading ? "正在核对选题的入库版本…"
      : !state.basket.length ? "把需要的题加入这里，跨试卷、跨筛选保留。"
      : `${Object.entries(counts).map(([type, count]) => `${QB.TYPE_NAMES[type] || "其他"} ${count} 题`).join(" · ") || "选题尚未载入"}${sources.size ? ` · 来自 ${sources.size} 份资料` : ""}`;
    const error = $("basketLoadError");
    error.replaceChildren();
    error.hidden = !state.basketError && !state.basketMissing.length;
    if (!error.hidden) {
      error.append(node("span", "", state.basketError || `${state.basketMissing.length} 道选题已撤回、更新或不存在，请处理后组卷。`));
      const retry = node("button", "button button-small", "重试选题");
      retry.type = "button";
      retry.addEventListener("click", () => refreshBasket({ force: true }));
      error.append(retry);
    }
  }

  // 1.12.10：批量条吸顶以后，两件事得跟着它走 ——
  // ① 它占多高，量出来写进 --library-bulk-h，跳题时给题目让出这么高；
  // ② 它吸住了没有，吸住了加个 .stuck（阴影在 CSS 里）。
  // 用 ResizeObserver 量高度：勾选变化会让按钮出现/消失、窄屏会排成两行、
  // 窗口变窄会重新折行 —— 这三种情况滚动监听都看不见，只有观察尺寸能跟上。
  const bulkBar = document.querySelector(".library-bulk");
  const measureBulkBar = () => {
    if (!bulkBar) return;
    const hidden = getComputedStyle(bulkBar).display === "none";
    // 写在 :root 上而不是条自己身上：题卡是条的后代兄弟，两边才都读得到。
    document.documentElement.style.setProperty("--library-bulk-h", hidden ? "0px" : `${bulkBar.offsetHeight}px`);
  };
  if (bulkBar && window.ResizeObserver) new window.ResizeObserver(measureBulkBar).observe(bulkBar);
  measureBulkBar();

  let bulkFrame = 0;
  const syncBulkBar = () => {
    if (bulkFrame) return;
    bulkFrame = requestAnimationFrame(() => {
      bulkFrame = 0;
      if (!bulkBar) return;
      const top = document.querySelector(".topbar")?.offsetHeight || 56;
      bulkBar.classList.toggle("stuck", bulkBar.getBoundingClientRect().top <= top + 1 && window.scrollY > 0);
      measureBulkBar();
    });
  };
  window.addEventListener("scroll", syncBulkBar, { passive: true });
  window.addEventListener("resize", syncBulkBar, { passive: true });
  syncBulkBar();

  function questionSignature(item) {
    return JSON.stringify([item, state.features, state.ai.mode, state.basket.includes(item.id)]);
  }

  function openQuestion(item) {
    const dialog = $("questionDialog");
    const refreshing = dialog.open && state.questionReturnId === item.id;
    const scroll = refreshing ? dialog.scrollTop : 0;
    $("questionTitle").textContent = `${item.source_filename} · ${sourceLocator(item)}`;
    $("questionVersion").textContent = `${QB.TYPE_NAMES[item.question_type] || item.question_type} · 第 ${item.version} 版 · ${formatDate(item.published_at)} 入库${item.review?.source === "ai" ? " · AI 审核，待人工核对" : ""}`;
    const paper = $("questionContent");
    QB.renderQuestion(paper, item.content, { showNumber: false, showAnswer: "none" });
    const reveal = answerReveal(item);
    if (reveal) paper.append(reveal);
    else paper.append(node("p", "helper", "原卷未提供答案与解析。"));
    const extras = extrasNode(item);
    if (extras) paper.append(extras);
    const actions = $("questionActions");
    actions.replaceChildren();
    const source = iconButton("button", "button button-small", "查看出处", "source");
    source.addEventListener("click", () => openSource(item));
    const add = iconButton("button", "button button-primary button-small", state.basket.includes(item.id) ? "移出试题篮" : "加入试题篮", state.basket.includes(item.id) ? "check" : "plus");
    add.addEventListener("click", () => {
      if (!state.basket.includes(item.id) && state.basket.length >= 500) { toast("试题篮最多放 500 题，请先保存一份组卷", "error"); return; }
      state.basket = state.basket.includes(item.id) ? state.basket.filter((id) => id !== item.id) : workspace.uniqueIds([...state.basket, item.id]);
      saveBasket();
      render();
      openQuestion(item);
    });
    const editAnswer = iconButton("button", "button button-small", "编辑答案解析", "plus");
    editAnswer.addEventListener("click", () => openAnswerEditor([item], { scope: "library" }));
    const editQuestion = node("button", "button button-small", "修改题目");
    editQuestion.type = "button"; editQuestion.addEventListener("click", () => openQuestionEditor(item));
    actions.append(source, editQuestion, editAnswer, ...jobButtons(item), node("span", "actions-spacer"), add);
    if (!dialog.open) { state.questionReturnFocus = document.activeElement; dialog.showModal(); }
    state.questionReturnId = item.id;
    state.questionSignature = questionSignature(item);
    if (refreshing) dialog.scrollTop = scroll;
    paper.scrollTop = 0;
    QB.fitOptions(paper);
    requestAnimationFrame(() => readingOverflowHints(paper));
  }

  async function withdrawItem(item) {
    const ok = await confirmDialog({
      title: "从正式题库撤回这道题？",
      text: `“${item.source_filename} ${sourceLocator(item)}”会从正式题库撤下。题卡会保留，可以重新入库。`,
      ok: "撤回", danger: true
    });
    if (!ok) return;
    try {
      const response = await fetch(`/api/library/${encodeURIComponent(item.id)}/withdraw`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: "{}"
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "撤回失败");
      state.basket = state.basket.filter((id) => id !== item.id);
      state.selected.delete(item.id);
      state.catalog.delete(item.id);
      saveBasket();
      toast(`已撤回“${item.source_filename} ${sourceLocator(item)}”`, "success");
      load();
    } catch (error) {
      setStatus(error.message || "撤回失败。", "warn");
      toast(error.message || "撤回失败", "error");
    }
  }

  // 1.12.6：一份卷一次要撤 25 道，逐题点「撤回」要点 25 下。
  // 确认框按来源任务分组列出卷名、题数和录入时间 —— 录过两次的卷同名，
  // 不分组就分不清这次撤的是哪一份，撤错了没法补救。
  async function withdrawSelected() {
    const picked = [...state.selected].map((id) => state.catalog.get(id)).filter(Boolean);
    if (!picked.length) return;
    const groups = new Map();
    picked.forEach((item) => {
      const key = item.document_id || item.source_filename;
      if (!groups.has(key)) groups.set(key, { name: item.source_filename, items: [], at: sourceFirstSeen(item.document_id, item.published_at) });
      groups.get(key).items.push(item);
    });
    const lines = [...groups.values()].map((group) => `《${group.name}》：${group.items.length} 道题，录于 ${shortDate(group.at)}`);
    const ok = await confirmDialog({
      title: groups.size > 1 ? `从正式题库撤回这 ${picked.length} 道题？` : "从正式题库撤回这些题？",
      text: `将撤下 ${picked.length} 道题，来自 ${groups.size} 份资料：\n${lines.join("\n")}\n\n`
        + "题卡和原卷都保留，撤回之后重新打勾就能再入库。",
      ok: "撤回所选", danger: true
    });
    if (!ok) return;
    const button = $("withdrawSelected");
    button.disabled = true;
    try {
      const response = await fetch("/api/library/withdraw-batch", {
        method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
        body: JSON.stringify({ ids: picked.map((item) => item.id) })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "撤回失败");
      const done = body.withdrawn_count ?? (body.withdrawn || []).length;
      const skipped = body.skipped || [];
      const withdrawn = new Set(body.withdrawn || []);
      state.basket = state.basket.filter((id) => !withdrawn.has(id));
      picked.forEach((item) => { if (withdrawn.has(item.id)) { state.selected.delete(item.id); state.catalog.delete(item.id); } });
      saveBasket();
      await load();
      const skippedText = skipped.length
        ? `；${skipped.length} 道没撤成（${[...new Set(skipped.map((row) => row.reason))].join("、")}）` : "";
      toast(`已撤回 ${done} 道题${skippedText}`, skipped.length ? "error" : "success");
    } catch (error) {
      setStatus(error.message || "撤回失败。", "warn");
      toast(error.message || "撤回失败", "error");
    } finally {
      button.disabled = !state.selected.size;
    }
  }

  // ---------------------------------------------------------------- 出处

  function box(bbox, kind) {
    const element = node("span", `source-box ${kind}`);
    element.style.left = `${bbox[0] / 10}%`;
    element.style.top = `${bbox[1] / 10}%`;
    element.style.width = `${(bbox[2] - bbox[0]) / 10}%`;
    element.style.height = `${(bbox[3] - bbox[1]) / 10}%`;
    return element;
  }

  const sourceState = { item: null, mode: "question", zoom: 1, token: 0 };
  const SOURCE_MIN_ZOOM = 0.25, SOURCE_MAX_ZOOM = 4;
  let sourcePan = null;

  function sourceReady() {
    return Array.from(ui.sourcePages.querySelectorAll(".source-surface img"))
      .some((image) => image.complete && image.naturalWidth > 0);
  }

  function syncSourceZoom() {
    const ready = sourceReady();
    ui.sourceZoom.textContent = `${Math.round(sourceState.zoom * 100)}%`;
    ui.sourceZoomOut.disabled = !ready || sourceState.zoom <= SOURCE_MIN_ZOOM;
    ui.sourceZoomIn.disabled = !ready || sourceState.zoom >= SOURCE_MAX_ZOOM;
    ui.sourceFit.disabled = !ready;
    ui.sourcePages.classList.toggle("can-pan", ready);
  }

  function stopSourcePan() {
    if (!sourcePan) return;
    const pointerId = sourcePan.pointerId;
    sourcePan = null;
    ui.sourcePages.classList.remove("panning");
    if (ui.sourcePages.hasPointerCapture(pointerId)) ui.sourcePages.releasePointerCapture(pointerId);
  }

  function zoomSource(value, clientX, clientY) {
    const contents = ui.sourcePages.querySelector(".source-content");
    if (!contents || !sourceReady()) return;
    const next = Math.min(SOURCE_MAX_ZOOM, Math.max(SOURCE_MIN_ZOOM, Math.round(value * 100) / 100));
    if (next === sourceState.zoom) return;
    stopSourcePan();
    const viewport = ui.sourcePages.getBoundingClientRect();
    const x = clientX ?? viewport.left + ui.sourcePages.clientWidth / 2;
    const y = clientY ?? viewport.top + ui.sourcePages.clientHeight / 2;
    // Keep the point on the page under the mouse stable, including multi-page sources.
    const surfaces = Array.from(contents.querySelectorAll(".source-surface"))
      .filter((surface) => surface.querySelector("img")?.naturalWidth > 0);
    const surface = surfaces.find((surface) => {
      const rect = surface.getBoundingClientRect();
      return y >= rect.top && y <= rect.bottom;
    }) || surfaces.reduce((nearest, surface) => {
      const rect = surface.getBoundingClientRect();
      const distance = Math.max(rect.top - y, y - rect.bottom, 0);
      return !nearest || distance < nearest.distance ? { surface, distance } : nearest;
    }, null)?.surface;
    const before = surface?.getBoundingClientRect();
    const relX = before?.width ? (x - before.left) / before.width : 0;
    const relY = before?.height ? (y - before.top) / before.height : 0;
    sourceState.zoom = next;
    contents.style.width = `${next * 100}%`;
    syncSourceZoom();
    if (before) {
      const after = surface.getBoundingClientRect();
      ui.sourcePages.scrollLeft += after.left + relX * after.width - x;
      ui.sourcePages.scrollTop += after.top + relY * after.height - y;
    }
  }

  function fitSourceWindow() {
    const surface = Array.from(ui.sourcePages.querySelectorAll(".source-surface"))
      .find((surface) => surface.querySelector("img")?.naturalWidth > 0);
    if (!surface) return;
    const style = getComputedStyle(ui.sourcePages);
    const width = ui.sourcePages.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    const height = ui.sourcePages.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) - 26;
    const rect = surface.getBoundingClientRect();
    const fitted = Math.min(1, width / (rect.width / sourceState.zoom), height / (rect.height / sourceState.zoom));
    zoomSource(Math.floor(fitted * 100) / 100);
    ui.sourcePages.scrollTo({ left: 0, top: 0 });
  }

  function sourceRegions(item) {
    const content = item.content || {};
    return [
      ...(content.sources || []).filter((source) => source.type !== "image").map((source) => ({ ...source, kind: "text" })),
      ...(content.figures || []).flatMap((figure) => [figure, ...(figure.parts || [])].map((part) => ({ ...part, kind: "figure" })))
    ].filter((region) => Number.isInteger(region.page_idx) && region.page_idx >= 0 && Array.isArray(region.bbox)
      && region.bbox.length === 4 && region.bbox.every(Number.isFinite)
      && region.bbox[0] >= 0 && region.bbox[1] >= 0 && region.bbox[2] <= 1000 && region.bbox[3] <= 1000
      && region.bbox[2] > region.bbox[0] && region.bbox[3] > region.bbox[1]);
  }

  function reviewLabel(item) {
    return item.review?.source === "ai" ? `${item.review.agent || "AI"} 审核 · 待人工核对` : "人工核对";
  }

  async function fetchPublication(id, compare = "") {
    const query = compare ? `?compare=${encodeURIComponent(compare)}` : "";
    const response = await fetch(`/api/library/${encodeURIComponent(id)}${query}`, { cache: "no-store" });
    if (!response.ok) {
      let message = `读取失败（${response.status}）`;
      try { message = (await response.json()).error || message; } catch { /* 不是 JSON 就用状态码 */ }
      throw new Error(message);
    }
    return response.json();
  }

  function renderRelated(container, body) {
    container.replaceChildren();
    container.open = false;
    const groups = [
      ["题面相同的其他资料", body.related_sources || []],
      ["题面相近的其他资料（需核对）", body.possible_sources || []]
    ].filter(([, items]) => items.length);
    container.hidden = groups.length === 0;
    if (!groups.length) return;
    container.append(node("summary", "", `其他资料中的相关题目（${groups.reduce((sum, [, items]) => sum + items.length, 0)}）`),
      node("p", "helper", "每份资料保留各自的出处和入库历史。题面相近的记录，请对照原卷确认。"));
    groups.forEach(([label, items]) => {
      const group = node("div", "related-source-group");
      group.append(node("h4", "", label));
      items.forEach((item) => {
        const row = node("div", "related-source-row");
        const name = node("div", "related-source-name");
        name.append(node("strong", "", `${item.source_filename} · ${sourceLocator(item)}`));
        if (item.origin) name.append(node("span", "helper", `题源：${item.origin}`));
        name.append(node("span", "helper", `${item.version_count || 1} 个入库版本 · ${reviewLabel(item)}`));
        const source = node("button", "button button-small", "查看原卷");
        source.type = "button";
        source.addEventListener("click", () => openSource(item));
        row.append(name, source);
        group.append(row);
      });
      container.append(group);
    });
    if (body.related_sources_truncated || body.possible_sources_truncated) {
      container.append(node("p", "helper", "这里只展示部分相关资料，可在题库搜索题干查找更多。"));
    }
  }

  function renderSource() {
    const item = sourceState.item;
    if (!item) return;
    stopSourcePan();
    ui.sourceTitle.textContent = `${item.source_filename} · ${sourceLocator(item)}`;
    ui.sourceReviewLink.href = item.document_id ? `/?document=${encodeURIComponent(item.document_id)}${item.draft_id ? `&draft=${encodeURIComponent(item.draft_id)}` : ""}` : "/";
    ui.sourceReviewLink.hidden = !item.document_id;
    ui.sourcePages.replaceChildren();
    const regions = sourceRegions(item);
    const available = Boolean(item.document_id && regions.length);
    ui.sourceQuestion.disabled = ui.sourceWholePage.disabled = ui.sourceFit.disabled = !available;
    ui.sourceQuestion.setAttribute("aria-pressed", String(sourceState.mode === "question"));
    ui.sourceWholePage.setAttribute("aria-pressed", String(sourceState.mode === "page"));
    syncSourceZoom();
    if (!item.document_id || !regions.length) {
      ui.sourcePages.append(node("p", "helper", "这道题没有保存可定位的原卷坐标，或原试卷已从本机删除。"));
      return;
    }
    const contents = node("div", "source-content");
    contents.style.width = `${sourceState.zoom * 100}%`;
    ui.sourcePages.append(contents);
    const pages = [...new Set(regions.map((region) => region.page_idx))].sort((a, b) => a - b);
    pages.forEach((page) => {
      const onPage = regions.filter((region) => region.page_idx === page);
      const cropped = sourceState.mode === "question";
      const bounds = cropped ? [
        Math.max(0, Math.min(...onPage.map((r) => r.bbox[0])) - 8),
        Math.max(0, Math.min(...onPage.map((r) => r.bbox[1])) - 8),
        Math.min(1000, Math.max(...onPage.map((r) => r.bbox[2])) + 8),
        Math.min(1000, Math.max(...onPage.map((r) => r.bbox[3])) + 8)
      ] : [0, 0, 1000, 1000];
      const [x0, y0, x1, y1] = bounds;
      const rw = x1 - x0, rh = y1 - y0;
      const frame = node("figure", "source-page");
      const surface = node("div", `source-surface${cropped ? " source-cropped" : ""}`);
      const image = node("img");
      image.alt = `原卷第 ${page + 1} 页${cropped ? "本题范围" : ""}`;
      image.draggable = false;
      if (cropped) {
        image.style.width = `${100000 / rw}%`;
        image.style.left = `${-100 * x0 / rw}%`;
        image.style.top = `${-100 * y0 / rh}%`;
      }
      image.src = `/api/documents/${encodeURIComponent(item.document_id)}/pages/${page}/preview`;
      image.addEventListener("error", () => {
        surface.classList.remove("source-cropped");
        surface.style.aspectRatio = "auto";
        surface.replaceChildren(node("p", "helper source-unavailable", "这页原卷暂时打不开，请检查本机原卷文件是否还在。"));
        syncSourceZoom();
      }, { once: true });
      surface.append(image);
      onPage.forEach((region) => {
        const adjusted = [
          (region.bbox[0] - x0) * 1000 / rw, (region.bbox[1] - y0) * 1000 / rh,
          (region.bbox[2] - x0) * 1000 / rw, (region.bbox[3] - y0) * 1000 / rh
        ];
        surface.append(box(adjusted, region.kind));
      });
      frame.append(surface, node("figcaption", "", `第 ${page + 1} 页${cropped ? " · 本题范围" : " · 原卷位置"}`));
      contents.append(frame);
      image.addEventListener("load", () => {
        if (!surface.isConnected) return;
        if (cropped) surface.style.aspectRatio = `${rw * image.naturalWidth} / ${rh * image.naturalHeight}`;
        syncSourceZoom();
        const first = surface.querySelector(".source-box");
        if (!cropped && first && pages[0] === page) first.scrollIntoView({ block: "nearest" });
      }, { once: true });
    });
    ui.sourcePages.scrollTo({ top: 0, left: 0 });
    syncSourceZoom();
  }

  function openSource(item) {
    const token = ++sourceState.token;
    sourceState.item = item;
    sourceState.mode = "question";
    sourceState.zoom = 1;
    ui.sourceRelated.replaceChildren();
    ui.sourceRelated.hidden = true;
    renderSource();
    if (!ui.sourceDialog.open) ui.sourceDialog.showModal();
    fetchPublication(item.id).then((body) => {
      if (token === sourceState.token && ui.sourceDialog.open) renderRelated(ui.sourceRelated, body);
    }).catch(() => { /* 保存的原卷仍可查看，关联资料读取失败不遮住它。 */ });
  }

  // ---------------------------------------------------------------- 组卷

  let printAnswersPreference = ui.printAnswers.checked;
  const printState = { token: 0, items: [], missing: [], dropped: [], loading: false, exporting: false, availableAnswers: 0, tooWide: 0, returnFocus: null,
    optionOverrides: {}, answerSpaceOverrides: {}, figureOverrides: {}, questionBreaks: [], activeToolId: null, layoutToken: 0, layoutPending: false, layoutError: "",
    // 排版是异步的：A4 分页跑完会把整卷重排一遍，刚才拿到焦点的按钮随之被换掉。
    // 这里记下「排完之后焦点该放回哪」，等重排结束再兑现（见 applyPrintFocus）。
    printFocus: null,
    // autoOrigin：这些题的「origin」是打开预览时因为没答案自动补的默认，不是这份卷子的选择。
    solutions: {}, autoOrigin: new Set(), solutionRecords: new Map(), missingAcknowledged: "" };
  const printNames = new Map();
  let answerEditor = null;
  let questionEditor = null;
  function openQuestionEditor(item) {
    if (!questionEditor) questionEditor = window.LibraryQuestionEditor.create({ node, QB, notify: toast, confirm: confirmDialog,
      onSaved: (before, after) => {
        state.catalog.set(after.id, after);
        if ($("questionDialog").open && state.questionReturnId === before.id) openQuestion(after);
        void load({ quiet: true });
        // 1.13.6：题面一改，篮里那道旧版本就作废了。这里原来什么都不管，篮里那一行
        // 照旧看着好好的 —— 老师点进去打开的是**改之前**那一份，会把刚改掉的错别字
        // 原样印到卷子上。重新核一遍，让它如实说「已经有新版本了」，再由老师决定换不换。
        void refreshBasket({ force: true });
      } });
    return questionEditor.open(item);
  }
  let answerRefreshPending = false;
  let answerPrintContext = null;
  let answerRefreshScheduled = false, answerRefreshWaiting = false;
  const answerChangedIds = new Set();
  const sameAnswerContext = context => context && context.token === printState.token
    && context.draftId === (state.draft?.id || null);
  function refreshSavedAnswerRows(ids) {
    syncPrintAnswers(printState.items);
    if (currentPrintOptions().document === "questions") { syncExportButtons(); return; }
    const flow = printState.layoutSource;
    const rows = flow ? Array.from(flow.querySelectorAll(".print-answer-row")) : [];
    const replacements = [...ids].map(id => ({ item: printState.items.find(item => item.id === id), row: rows.find(row => row.dataset.questionId === id) }));
    if (!flow || replacements.some(value => !value.item || !value.row)) { renderPrint(printState.items); return; }
    // The source already holds the original typeset question bodies. Replace
    // only edited answers and paginate once, preserving all unedited math/images.
    replacements.forEach(({ item, row }) => {
      const inline = row.classList.contains("print-answer-inline");
      if (inline && !printAnswerContent(item)) row.remove();
      else row.replaceWith(printAnswerRow(groupedQuestionNumber(item), item, inline));
    });
    printState.layoutPromise = refreshPrintPages(flow);
  }
  function deferAnswerRefresh(context, sync, id) {
    answerRefreshPending ||= sync;
    if (sameAnswerContext(context)) {
      if (!sameAnswerContext(answerPrintContext)) answerChangedIds.clear();
      answerPrintContext = context; if (id) answerChangedIds.add(id);
    }
    const schedule = () => {
      if (answerRefreshScheduled) return;
      answerRefreshScheduled = true;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const flush = () => {
          answerRefreshScheduled = false;
          if (answerEditor?.isOpen()) { waitForClose(); return; }
          const refreshLibrary = answerRefreshPending, refreshContext = answerPrintContext, ids = [...answerChangedIds];
          answerRefreshPending = false; answerPrintContext = null; answerChangedIds.clear();
          if (refreshLibrary) render();
          if (sameAnswerContext(refreshContext) && ui.sheet.open) refreshSavedAnswerRows(ids);
        };
        // Closing paints first. Expensive A4 work begins in a later idle/task,
        // and a reopened editor postpones it again without duplicating listeners.
        if (window.requestIdleCallback) window.requestIdleCallback(flush, { timeout: 600 });
        else window.setTimeout(flush, 0);
      }));
    };
    const waitForClose = () => {
      if (answerRefreshWaiting) return;
      answerRefreshWaiting = true;
      $("answerEditorDialog")?.addEventListener("close", () => { answerRefreshWaiting = false; schedule(); }, { once: true });
    };
    if (answerEditor?.isOpen()) waitForClose(); else schedule();
  }
  function openAnswerEditor(items = printState.items, options = {}) {
    if (printState.exporting || state.draftSaving) { toast("请等待当前导出或保存完成"); return; }
    if (!items.length) { toast("先选题，再补充答案解析"); return; }
    if (!answerEditor) answerEditor = window.LibraryAnswerEditor.create({ node, QB, notify: toast, confirm: confirmDialog,
      onSaved: (item, solution, { sync, scope, scopeContext }) => {
        if (scope === "paper" && sameAnswerContext(scopeContext)) {
          printState.solutions[item.id] = solution.id; printState.solutionRecords.set(solution.id, solution);
          const chosen = printState.items.find(value => value.id === item.id); if (chosen) { chosen.solution = solution; chosen.solution_revision = solution.id; }
          markDraftDirty();
          if (ui.sheet.open && currentPrintOptions().document !== "questions") {
            // Until the deferred answer rows are laid out, old visible pages
            // must not be printed/exported as if they contain the saved edit.
            printState.layoutPending = true; syncExportButtons();
          }
        }
        if (sync) {
          const stored = state.catalog.get(item.id); if (stored) stored.solution = solution;
          for (const value of state.items) if (value.id === item.id) value.solution = solution;
        }
        deferAnswerRefresh(scope === "paper" ? scopeContext : null, sync, item.id);
      } });
    return answerEditor.open(items, { ...options, scopeContext: { token: printState.token, draftId: state.draft?.id || null } });
  }

  function printAnswerRow(number, item, inline = false) {
    const row = node("div", `print-answer-row${inline ? " print-answer-inline" : ""}`);
    row.dataset.questionId = item.id;
    row.append(node("strong", "", inline ? "" : `${number}.`));
    const body = node("div"), selected = printAnswerContent(item);
    solutions.render(body, selected?.content, { node, QB, empty: "（原卷未提供答案解析）" });
    if (selected?.ai) body.prepend(node("span", "print-ai-note", "（AI 参考，未核对）"));
    row.append(body); return row;
  }

  async function checkMissingAnswers(format) {
    if ($("printDocument").value === "questions" && format !== "split") return true;
    const items = printState.items.filter(item => !printAnswerContent(item));
    if (!items.length) return true;
    const signature = JSON.stringify(items.map(item => item.id));
    if (printState.missingAcknowledged === signature) return true;
    const dialog = $("answerMissingDialog");
    $("answerMissingText").textContent = `第 ${items.map(item => groupedQuestionNumber(item)).join("、")} 题缺少答案解析。可以一次补齐，也可以本次跳过；原卷与题库内容保留。`;
    dialog.returnValue = ""; dialog.showModal();
    const decision = await new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue), { once: true }));
    if (decision === "skip") { printState.missingAcknowledged = signature; return true; }
    if (["manual", "ai"].includes(decision)) {
      await openAnswerEditor(printState.items.map(item => ({ ...item, exam_number: groupedQuestionNumber(item) })), { scope: "paper", focus: items[0].id,
        selected: decision === "ai" ? items.map(item => item.id) : [] });
    }
    return false;
  }

  function groupedQuestionNumber(item) {
    const order = ["single_choice", "multiple_choice", "fill_blank", "true_false", "free_response"];
    const ranked = printState.items.map((value, index) => ({ value, index })).sort((a, b) => {
      const rank = value => order.indexOf(value.question_type) < 0 ? order.length : order.indexOf(value.question_type);
      return rank(a.value) - rank(b.value) || a.index - b.index;
    });
    return ranked.findIndex(value => value.value.id === item.id) + 1;
  }

  function normalizePrintOptions(options = {}) {
    // 1.12.9：默认只出题目。要答案的人自己切「题目＋答案」，多出来的是
    // 一次点击，少出来的是一份混在题目卷里、老师得自己划掉的答案。
    // 什么都不给 → 题目；老草稿只有 answers、没有 document 的，照它当时的意思走
    // （服务端 library_drafts._print_options() 也是这么推的，两边必须一样，
    // 不然一个存了 answers 的老草稿会在某一侧被悄悄改成题目卷）。
    const document = ["questions", "answers", "combined"].includes(options.document)
      ? options.document
      : "answers" in options ? (options.answers ? "combined" : "questions") : "questions";
    return {
      answers: document !== "questions", origin: options.origin === true, ai_answers: options.ai_answers === true,
      answer_layout: options.answer_layout === "appendix" ? "appendix" : "inline",
      document, font_size: [12, 14, 16].includes(Number(options.font_size)) ? Number(options.font_size) : 12,
      answer_space: ["none", "small", "medium", "large"].includes(options.answer_space) ? options.answer_space : "none",
      answer_space_overrides: Object.fromEntries(Object.entries(options.answer_space_overrides || {}).filter(([, value]) => ["none", "small", "medium", "large"].includes(value))),
      student_info: options.student_info !== false,
      pagination: options.pagination === "keep" ? "keep" : "compact",
      option_layout: ["auto", "four", "two", "vertical"].includes(options.option_layout) ? options.option_layout : "auto",
      option_overrides: Object.fromEntries(Object.entries(options.option_overrides || {}).filter(([, value]) => ["auto", "four", "two", "vertical"].includes(value))),
      question_breaks: Array.isArray(options.question_breaks) ? [...new Set(options.question_breaks.filter(id => typeof id === "string"))] : [],
      figure_overrides: normalizeFigureOverrides(options.figure_overrides)
    };
  }

  // 老师在预览里对配图做的调整，跟着这份草稿存。服务端 library_drafts 也查一遍，
  // 这里的检查不是多余：这份设置最终要同时喂给预览排版、PDF 和 Word，三边都得先
  // 收成同一种形状，谁也不该在下游处理「图宽是 -20」这种事。
  const FIGURE_WIDTH_MIN = 5, FIGURE_WIDTH_MAX = 178;
  const FIGURE_POSITIONS = ["left", "center", "right"];
  function normalizeFigureOverrides(value) {
    const result = {};
    Object.entries(value || {}).forEach(([id, setting]) => {
      if (!setting || typeof setting !== "object") return;
      const entry = {};
      const widths = {};
      Object.entries(setting.width || {}).forEach(([key, size]) => {
        const index = Number(key), millimetres = Number(size);
        if (Number.isInteger(index) && index >= 0 && millimetres >= FIGURE_WIDTH_MIN && millimetres <= FIGURE_WIDTH_MAX) {
          widths[String(index)] = Math.round(millimetres * 10) / 10;
        }
      });
      if (Object.keys(widths).length) entry.width = widths;
      if (FIGURE_POSITIONS.includes(setting.align) && setting.align !== "center") entry.align = setting.align;
      const breaks = [...new Set((Array.isArray(setting.breaks) ? setting.breaks : [])
        .map(Number).filter(index => Number.isInteger(index) && index > 0))].sort((a, b) => a - b);
      if (breaks.length) entry.breaks = breaks;
      if (Object.keys(entry).length) result[id] = entry;
    });
    return result;
  }

  function currentPrintOptions() {
    return normalizePrintOptions({
      document: $("printDocument").value, origin: ui.printOrigin.checked,
      answer_layout: $("printAnswerLayout").value,
      ai_answers: Boolean(state.features.ai_answer && ui.printAi.checked),
      font_size: $("printFontSize").value, answer_space: $("printDocument").value === "combined" && $("printAnswerLayout").value === "inline" ? "none" : $("printAnswerSpace").value,
      student_info: $("printStudentInfo").checked,
      pagination: $("printPagination").value, option_layout: $("printOptionLayout").value,
      option_overrides: Object.fromEntries(Object.entries(printState.optionOverrides || {}).filter(([id]) => !state.basket || state.basket.includes(id))),
      answer_space_overrides: Object.fromEntries(Object.entries(printState.answerSpaceOverrides || {}).filter(([id]) => !state.basket || state.basket.includes(id))),
      figure_overrides: Object.fromEntries(Object.entries(printState.figureOverrides || {}).filter(([id]) => !state.basket || state.basket.includes(id))),
      question_breaks: (printState.questionBreaks || []).filter(id => !state.basket || state.basket.includes(id))
    });
  }

  function prunePrintChoices() {
    const ids = new Set(state.basket);
    printState.optionOverrides = Object.fromEntries(Object.entries(printState.optionOverrides || {}).filter(([id]) => ids.has(id)));
    printState.answerSpaceOverrides = Object.fromEntries(Object.entries(printState.answerSpaceOverrides || {}).filter(([id]) => ids.has(id)));
    printState.figureOverrides = Object.fromEntries(Object.entries(printState.figureOverrides || {}).filter(([id]) => ids.has(id)));
    printState.questionBreaks = (printState.questionBreaks || []).filter(id => ids.has(id));
    printState.solutions = solutions.fixedSelections(printState.solutions, state.basket);
  }

  function applyPrintOptions(options) {
    const restored = normalizePrintOptions(options);
    $("printDocument").value = restored.document;
    $("printFontSize").value = String(restored.font_size);
    $("printAnswerSpace").value = restored.answer_space;
    $("printStudentInfo").checked = restored.student_info;
    $("printPagination").value = restored.pagination;
    $("printOptionLayout").value = restored.option_layout;
    $("printAnswerLayout").value = options.answer_layout ? restored.answer_layout : "appendix";
    printState.optionOverrides = restored.option_overrides;
    printState.answerSpaceOverrides = restored.answer_space_overrides;
    printState.figureOverrides = restored.figure_overrides;
    printState.questionBreaks = restored.question_breaks;
    printAnswersPreference = restored.answers;
    ui.printAnswers.checked = restored.answers;
    ui.printOrigin.checked = restored.origin;
    ui.printAi.checked = Boolean(state.features.ai_answer && restored.ai_answers);
  }

  function syncExportButtons() {
    const blocked = printState.exporting || printState.loading || !!printState.missing.length || !printState.items.length;
    const answerEmpty = $("printDocument").value === "answers" && !printState.availableAnswers;
    $("printButton").disabled = blocked || answerEmpty || !!printState.tooWide;
    $("exportPdf").disabled = blocked || answerEmpty || !!printState.tooWide || printState.layoutPending || !!printState.layoutError;
    $("exportWord").disabled = blocked || answerEmpty;
    $("exportSplit").disabled = blocked || !printState.availableAnswers;
    // 1.13.6：一道题都没载入时，这个按钮原来还是可点的，按下去打开一个空编辑器，
    // 屏幕上什么也不发生 —— 比灰掉更难查。载入中或有未载入的题也一并按不可用算。
    const manage = $("managePrintAnswers");
    manage.disabled = printState.exporting || printState.loading || !printState.items.length;
    manage.title = printState.items.length ? "为这次组卷里的题补答案解析" : "先把未载入的选题处理掉，载入的题才能补答案解析";
    $("printOptionLayout").disabled = printState.exporting || (printState.items.length > 0 && printState.items.every(item => item.content?.body_mode === "source_image"));
  }

  function printAnswerContent(item) {
    return solutions.selected(item, { ai_answers: Boolean(ui.printAi.checked && state.features.ai_answer) });
  }

  function syncPrintAnswers(items) {
    const available = items.filter((item) => printAnswerContent(item)).length;
    printState.availableAnswers = available;
    const aiAvailable = state.features.ai_answer && items.some((item) =>
      !String(item.content?.answer ?? "").trim()
      && (String(item.ai_answer?.answer ?? "").trim() || String(item.ai_answer?.analysis ?? "").trim()));
    ui.printAiBox.hidden = !aiAvailable;
    ui.printAnswers.disabled = available === 0;
    printAnswersPreference = $("printDocument").value !== "questions";
    ui.printAnswers.checked = available > 0 && printAnswersPreference;
    let status = $("printAnswerStatus");
    if (!status) {
      status = node("p", "print-answer-status");
      status.id = "printAnswerStatus";
      status.setAttribute("role", "status");
      status.setAttribute("aria-live", "polite");
      ui.sheet.querySelector(".print-options-body").append(status);
      ui.printAnswers.setAttribute("aria-describedby", status.id);
    }
    status.textContent = !items.length ? "请先加入题目。"
      : !available ? (aiAvailable
        ? "没有原卷答案，可选择附上明确标注的 AI 参考（未核对）。"
        : $("printDocument").value === "answers" ? "这些题没有答案或解析，不能导出空答案卷。请改选题目卷。"
          : "这些题没有答案或解析，仅输出题目卷。")
      : available < items.length ? `${items.length} 题中 ${available} 题有答案或解析，其余标明原卷未提供。`
      : `${items.length} 题均有答案或解析。`;
    const resultOnly = items.filter(item => solutions.completeness(item, { ai_answers: Boolean(ui.printAi.checked && state.features.ai_answer) }) === "result_only");
    if (resultOnly.length) status.textContent += ` 第 ${resultOnly.map(item => groupedQuestionNumber(item)).join("、")} 题只有结果，可在“答案解析”中补充详细过程。`;
    const review = items.filter(item => item.solution_needs_review);
    if (review.length) status.textContent += ` 第 ${review.map(item => groupedQuestionNumber(item)).join("、")} 题题面有改动，原有解析待核对。`;
    $("printDocument").querySelector('option[value="answers"]').disabled = !available;
    $("printAnswerLayoutBox").hidden = $("printDocument").value !== "combined";
    $("printAnswerSpace").disabled = $("printDocument").value === "combined" && $("printAnswerLayout").value === "inline";
    syncExportButtons();
  }

  async function resolvePrintSolutions(items) {
    await Promise.all(items.map(async item => {
      const revision = printState.solutions[item.id];
      // 「origin」有两种来路，待遇不能一样：
      //  · 存草稿时记下的（autoOrigin 里没有）—— 那时候这道题就这样，是这份卷子的选择，
      //    题库后来同步进来的解析不能顶掉它。
      //  · 打开预览时因为「当时没答案」自动补上的（autoOrigin 里有）—— 只是那一次的默认，
      //    用户在题卡上填了答案就该用新的。否则界面上明明写着「已保存到题库」，回到组卷
      //    还是「没有答案」，「分别导出题目卷与答案卷」一直灰着，刷新一下又好了。
      if (revision === "origin") {
        if (printState.autoOrigin.has(item.id) && item.solution?.id) {
          printState.solutions[item.id] = item.solution.id;
          printState.solutionRecords.set(item.solution.id, item.solution);
          printState.autoOrigin.delete(item.id);
          item.solution_revision = item.solution.id;
          return;
        }
        item.solution = null; item.solution_revision = "origin"; return;
      }
      if (!revision) {
        const selected = item.solution?.id || "origin";
        printState.solutions[item.id] = selected; item.solution_revision = selected;
        if (item.solution?.id) { printState.solutionRecords.set(selected, item.solution); printState.autoOrigin.delete(item.id); }
        else printState.autoOrigin.add(item.id);
        return;
      }
      let saved = printState.solutionRecords.get(revision);
      if (!saved) {
        const response = await fetch(`/api/library/${encodeURIComponent(item.id)}/solution?revision=${encodeURIComponent(revision)}`, { cache: "no-store" });
        const result = await response.json();
        if (!response.ok || !result.solution || result.solution.id !== revision) throw new Error(result.error || "保存的答案解析版本暂时无法读取，请重试");
        saved = result.solution; printState.solutionRecords.set(revision, saved);
      }
      item.solution = saved; item.solution_revision = revision;
    }));
  }

  async function openPrint() {
    const token = ++printState.token;
    const opening = !ui.sheet.open;
    if (opening && !state.draftDirty) state.draftBaseline = draftSignature();
    if (opening) printState.returnFocus = document.activeElement;
    if (!printState.settingsInitialized) {
      $("printSettings").open = !window.matchMedia("(max-width: 979px)").matches;
      printState.settingsInitialized = true;
    }
    ui.printAiBox.hidden = !state.features.ai_answer;
    ui.paper.replaceChildren(node("p", "helper", "正在准备…"));
    ui.sheet.hidden = false;
    if (opening) ui.sheet.showModal();
    document.body.classList.add("printing");
    printState.loading = true;
    syncExportButtons();
    $("printMissing").hidden = true;
    $("printLayoutNotice").hidden = true;
    let items = [], missing = [];
    const ids = [...state.basket];
    ids.forEach((id) => {
      const known = state.catalog.get(id);
      if (known) printNames.set(id, `${known.source_filename || "试卷"} · ${sourceLocator(known)}`);
    });
    try {
      // Revalidate every selected snapshot, including cards already displayed.
      // An old cached item must not print after it was withdrawn or superseded.
      const body = await batchItems(ids);
      const byId = new Map(body.items.map((item) => [item.id, item]));
      items = ids.map((id) => byId.get(id)).filter(Boolean).map(item => ({ ...item }));
      await resolvePrintSolutions(items);
      items.forEach((item) => { state.catalog.set(item.id, byId.get(item.id)); printNames.set(item.id, `${item.source_filename || "试卷"} · ${sourceLocator(item)}`); });
      missing = body.missing.map((item) => ({ ...item, label: printNames.get(item.id) || `未载入的第 ${ids.indexOf(item.id) + 1} 道选题` }));
    } catch (error) {
      missing = ids.map((id, index) => ({ id, label: printNames.get(id) || `未载入的第 ${index + 1} 道选题`, reason: error.message || "读取失败，请重试" }));
    }
    if (token !== printState.token || !ui.sheet.open) return;
    // Merely opening a paper can resolve its fixed answer revisions. That is
    // initialization, not an edit; a user's concurrent changes remain dirty.
    if (!state.draftDirty) state.draftBaseline = draftSignature();
    printState.loading = false;
    printState.missing = missing;
    syncDroppedQuestions();
    renderPrint(items);
    await printState.layoutPromise;
  }

  function markDraftDirty() {
    if (state.draftBaseline === null && (state.draft || state.basket.length)) state.draftDirty = true;
    state.draftDirty = hasUnsavedDraft();
    $("draftSaveStatus").textContent = state.draftDirty
      ? state.draft ? `“${state.draft.title}”有未保存的改动` : "当前组卷尚未保存为草稿"
      : state.draft ? `正在编辑“${state.draft.title}”` : "";
  }

  function draftPayload() {
    return { title: ui.printTitle.value.trim() || "练习", ids: [...state.basket],
      print_options: currentPrintOptions(), solutions: solutions.draftSelections(printState.solutions, state.basket) };
  }

  function draftSignature() {
    return JSON.stringify(draftPayload());
  }

  function hasUnsavedDraft() {
    // A cleared, unassociated basket has no paper that can be lost. A saved
    // draft's removed questions still count as a real, uncommitted edit.
    if (!state.draft && !state.basket.length) { state.draftBaseline = null; state.draftDirty = false; return false; }
    if (state.draftBaseline !== null) return state.draftDirty = draftSignature() !== state.draftBaseline;
    return state.draftDirty;
  }

  async function saveDraft({ copy = false } = {}) {
    if (state.draftSaving || printState.exporting) return;
    if (!state.basket.length) { toast("先选题，再保存组卷草稿", "error"); return; }
    const previousDraft = state.draft;
    const existing = !copy && state.draft;
    const payload = draftPayload();
    const submitted = JSON.stringify(payload);
    state.draftSaving = true;
    $("saveDraft").disabled = $("saveDraftAs").disabled = true;
    try {
      if (existing) payload.revision = existing.revision;
      const response = await fetch(existing ? `/api/library/drafts/${encodeURIComponent(existing.id)}` : "/api/library/drafts", {
        method: existing ? "PUT" : "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: JSON.stringify(payload) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "草稿未保存");
      if (state.draft === previousDraft) {
        state.draft = body.draft;
        state.draftBaseline = submitted;
        state.draftDirty = draftSignature() !== submitted;
        $("draftSaveStatus").textContent = `已保存“${body.draft.title}” · ${body.draft.ids.length} 题${state.draftDirty ? " · 后续改动尚未保存" : body.draft.validity?.valid === false ? " · 有选题需要处理" : ""}`;
      }
      toast("组卷草稿已保存", "success");
    } catch (error) { toast(error.message || "草稿未保存", "error"); $("draftSaveStatus").textContent = error.message || "草稿未保存"; }
    finally { state.draftSaving = false; $("saveDraft").disabled = $("saveDraftAs").disabled = false; }
  }

  async function openDrafts() {
    const dialog = $("draftsDialog");
    if (!dialog.open) dialog.showModal();
    const box = $("draftsList"), status = $("draftsStatus");
    box.replaceChildren();
    status.textContent = "正在读取草稿…";
    try {
      const response = await fetch("/api/library/drafts", { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "草稿读取失败");
      status.textContent = body.drafts.length ? `共 ${body.drafts.length} 份草稿 · 保存的题目版本与顺序保持不变` : "还没有组卷草稿。在组卷预览里点“保存草稿”，下次继续备课。";
      body.drafts.forEach((draft) => {
        const row = node("article", "draft-row");
        const info = node("div", "draft-info");
        info.append(node("strong", "", draft.title), node("p", "helper", `${draft.ids.length} 题 · ${formatDate(draft.updated_at)} 保存${draft.validity?.valid === false ? ` · ${draft.validity.missing.length} 道选题需要处理` : ""}`));
        const open = node("button", "button button-small", "继续组卷");
        open.type = "button";
        open.addEventListener("click", async () => {
          const openToken = state.draftOpenToken = (state.draftOpenToken || 0) + 1;
          if (state.draftDirty || (state.basket.length && JSON.stringify(state.basket) !== JSON.stringify(draft.ids))) {
            const ok = await confirmDialog({ title: "打开这份草稿？", text: "当前试题篮将换成草稿中的选题。已保存的其他草稿不变；当前未保存的选题请先保存。", ok: "打开草稿" });
            if (!ok) return;
          }
          if (openToken !== state.draftOpenToken) return;
          const currentSnapshot = JSON.stringify(draftPayload());
          // Re-read the revision so another window's saved changes aren't lost.
          try {
            const response = await fetch(`/api/library/drafts/${encodeURIComponent(draft.id)}`, { cache: "no-store" });
            const body = await response.json();
            if (!response.ok) throw new Error(body.error || "草稿读取失败");
            if (openToken !== state.draftOpenToken || !dialog.open) return;
            if (currentSnapshot !== JSON.stringify(draftPayload())) {
              toast("当前组卷已发生变化，请重新选择要打开的草稿", "error");
              return;
            }
            const current = body.draft;
            state.basket = workspace.uniqueIds(current.ids);
            state.draft = null;
            saveBasket();
            state.draft = current;
            printState.solutions = solutions.draftSelections(current.solutions, current.ids);
            // 草稿里的每一个版本号都是存下来的选择，包括那些「当时没答案」写下的 origin。
            printState.autoOrigin = new Set();
            state.draftDirty = false;
            ui.printTitle.value = current.title;
            applyPrintOptions(current.print_options);
            state.draftBaseline = draftSignature();
            $("draftSaveStatus").textContent = `正在编辑“${current.title}”`;
            dialog.close();
            render();
            await openPrint();
          } catch (error) { toast(error.message || "草稿读取失败", "error"); }
        });
        const remove = node("button", "button button-quiet button-small", "删除草稿");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          if (!await confirmDialog({ title: `删除“${draft.title}”？`, text: "只删除这份组卷草稿，题库中的题目和原卷都保留。", ok: "删除草稿", danger: true })) return;
          try {
            const response = await fetch(`/api/library/drafts/${encodeURIComponent(draft.id)}`, { method: "DELETE", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: "{}" });
            const body = await response.json();
            if (!response.ok) throw new Error(body.error || "草稿删除失败");
            if (state.draft?.id === draft.id) { state.draft = null; markDraftDirty(); }
            await openDrafts();
          } catch (error) { toast(error.message || "草稿删除失败", "error"); }
        });
        row.append(info, open, remove);
        box.append(row);
      });
    } catch (error) {
      status.textContent = error.message || "草稿读取失败";
      const retry = node("button", "button", "重试读取草稿");
      retry.type = "button";
      retry.addEventListener("click", openDrafts);
      box.append(retry);
    }
  }

  // 老师把题移出试题篮就是接受了这卷少一道题，但「少了几道」不能跟着一起消失 ——
  // 移出过的题已经不在篮里，openPrint 重载也不会再报 missing，提示只跟着 missing 走的话
  // 就会在这里悄悄清零。所以单独记一笔；之后加回篮里的题再从这笔记里划掉
  // （篮子回到原样，卷面也就完整了）。每次 openPrint 末尾调一次。
  function syncDroppedQuestions() {
    printState.dropped = printState.dropped.filter((row) => !state.basket.includes(row.id));
  }

  function renderPrintMissing() {
    const box = $("printMissing");
    const pending = printState.missing.length;
    const dropped = printState.dropped;
    box.replaceChildren();
    box.hidden = !pending && !dropped.length;
    if (box.hidden) return;
    // 「移出试题篮」把缺题赶走了，missing 随之清空、导出解锁、纸上的红框也没了。
    // 卷面从此比老师挑的那些题少几道，而界面上一句话都不剩 —— 印出去的卷子缺题，
    // 老师自己都不知道。所以已移出的题单独记着，提示不跟着 missing 一起消失。
    if (dropped.length) {
      box.append(node("p", "", pending
        ? `本卷有 ${pending} 题尚未载入，你已移出 ${dropped.length} 题；导出的卷面会少这 ${dropped.length} 道题。`
        : `你已把 ${dropped.length} 道题移出试题篮，本卷卷面比试题篮少 ${dropped.length} 道题。确认过再导出。`));
    }
    if (!pending) { return; }
    box.append(node("p", "", `试题篮 ${state.basket.length} 题，成功载入 ${printState.items.length} 题；以下 ${pending} 题尚未载入。处理后才能导出或打印。`));
    printState.missing.forEach((item) => {
      const row = node("div", "print-missing-row");
      const remove = node("button", "button button-small", "移出试题篮");
      remove.type = "button";
      remove.setAttribute("aria-label", `移出${item.label}`);
      remove.addEventListener("click", () => {
        state.basket = state.basket.filter((id) => id !== item.id);
        printState.missing = printState.missing.filter((row) => row.id !== item.id);
        printState.dropped = printState.dropped.filter((row) => row.id !== item.id).concat(item);
        saveBasket();
        renderPrint(printState.items);
        render();
        ($("retryPrintMissing") || $("closePrint")).focus();
      });
      row.append(node("span", "", `${item.label}：${missingReason(item)}`));
      if (item.replacement_id) {
        // 后端已经算好了该换成哪一版。题面改过以后篮里那道就废了，而老师多半
        // 正是刚改完题面回来接着组卷 —— 没有这一步就只剩「移出去再搜一遍」。
        const swap = node("button", "button button-small", "换成当前版本");
        swap.type = "button";
        swap.addEventListener("click", async () => {
          if (!swapBasketForReplacement(item)) return;
          await openPrint();
        });
        row.append(swap);
      }
      row.append(remove);
      box.append(row);
    });
    const retry = node("button", "button button-small", "重试未载入题目");
    retry.id = "retryPrintMissing";
    retry.type = "button";
    retry.addEventListener("click", openPrint);
    box.append(retry);
  }

  // 换序、移出之后，焦点要跟着题走。之前是 renderPrint 里同步 focus 一下就完事，
  // 可分页是异步的：等它跑完，整卷连同工具面板都被换掉了，焦点掉回 body，
  // 键盘用户按完回车就得从头 Tab 一遍。这里把请求留到重排之后再兑现。
  function applyPrintFocus() {
    const request = printState.printFocus;
    printState.printFocus = null;
    if (!request) return;
    const blocks = Array.from(ui.paper.querySelectorAll(".print-question"));
    if (!blocks.length) { $("closePrint").focus({ preventScroll: true }); return; }
    const block = request.id
      ? blocks.find(question => question.dataset.questionId === request.id)
      : blocks[Math.min(request.index ?? 0, blocks.length - 1)];
    if (!block) return;
    const wanted = request.action ? block.querySelector(`[data-action="${request.action}"]`) : null;
    const target = wanted && !wanted.disabled
      ? wanted
      : block.querySelector(".print-question-tools > summary, button:not(:disabled)");
    target?.focus({ preventScroll: true });
    block.scrollIntoView({ block: "nearest" });
  }

  function renderPrint(items) {
    // Any new source, including an empty basket, invalidates pending pages.
    ++printState.layoutToken;
    // 上一轮排完还没兑现的焦点请求就此作废：重排之后位置早就变了。
    printState.printFocus = null;
    printState.layoutPending = false;
    printState.layoutError = "";
    printState.layoutPromise = Promise.resolve();
    printState.items = items;
    $("printImageHint").hidden = !items.some(item => item.content?.body_mode === "source_image");
    renderPrintMissing();
    syncPrintAnswers(items);
    ui.paper.replaceChildren();
    const options = currentPrintOptions();
    ui.paper.style.setProperty("--exam-font-size", `${options.font_size}pt`);
    ui.paper.dataset.answerSpace = options.answer_space;
    ui.paper.dataset.document = options.document;
    const title = node("h2", "print-title", ui.printTitle.value.trim() || "练习");
    const info = node("p", "print-info", "姓名 ____________　班级 ____________　得分 ________");
    ui.paper.append(title);
    if (options.student_info && options.document !== "answers") ui.paper.append(info);
    if (!items.length) {
      delete ui.paper.dataset.pageCount;
      $("printPageStatus").textContent = "";
      $("printLayoutWarnings").hidden = true;
      ui.paper.append(node("p", "helper", printState.missing.length ? "选题尚未全部载入，请先重试或移出未载入题目。" : "试题篮是空的。"));
      $("printLayoutNotice").hidden = true;
      syncExportButtons();
      return;
    }
    // 分组顺序只有一份定义（导出那边也用同一份）：两边各写一份的话，后端报的
    // 「第 N 题」迟早和卷面上看到的对不上。ExamExport 没载入时退回本地那份。
    const knownTypes = new Set(["single_choice", "multiple_choice", "fill_blank", "true_false", "free_response"]);
    const ordered = window.ExamExport?.paperGroups
      ? window.ExamExport.paperGroups(items)
      : (() => {
        const out = [];
        for (const [key, name] of [["single_choice", "选择题"], ["multiple_choice", "多选题"],
          ["fill_blank", "填空题"], ["true_false", "判断题"], ["free_response", "解答题"]]) {
          const group = items.filter((item) => item.question_type === key);
          if (group.length) out.push([name, group]);
        }
        const other = items.filter((item) => !knownTypes.has(item.question_type));
        if (other.length) out.push(["其他", other]);
        return out;
      })();
    const chinese = ["一", "二", "三", "四", "五", "六"];
    let number = 0;
    const answers = [];
    ordered.forEach(([name, group], index) => {
      if (options.document !== "answers") ui.paper.append(node("h3", "print-section", `${chinese[index] || index + 1}、${name}`));
      group.forEach((item, position) => {
        number += 1;
        if (options.document !== "answers") {
          const block = node("div", "print-question");
          QB.renderQuestion(block, item.content, { number, showAnswer: "none" });
          const origin = item.origin || item.content?.origin;
          // 原图题只建 .qb-stem，没有 .qb-stem-body。两边的插入点必须一样，
// 否则这类题在屏幕上没题源、导出的卷子上有，老师照着屏幕核对会找不到。
if (ui.printOrigin.checked && origin) (block.querySelector(".qb-stem-body") || block.querySelector(".qb-stem"))?.prepend(node("span", "print-origin", `（${origin}）`));
          block.dataset.questionId = item.id;
          block.dataset.questionType = item.question_type;
          const space = window.ExamLayout.answerSpace(item.question_type, item.id, options);
          if (space !== "none") {
            const blank = node("div", "print-answer-space"); blank.dataset.answerSpace = space;
            block.append(blank);
          }
          block.append(printTools(items, group, position, number));
          ui.paper.append(block);
          if (options.document === "combined" && options.answer_layout === "inline" && ui.printAnswers.checked && printAnswerContent(item)) ui.paper.append(printAnswerRow(number, item, true));
        }
        answers.push([number, item]);
      });
    });
    if (options.document !== "questions" && ui.printAnswers.checked && !(options.document === "combined" && options.answer_layout === "inline")) {
      const key = node("section", "print-answers");
      if (options.document === "answers") key.classList.add("print-answers-only");
      key.append(node("h3", "print-section", "参考答案与解析"));
      answers.forEach(([index, item]) => {
        key.append(printAnswerRow(index, item));
      });
      ui.paper.append(key);
    }
    if (options.document === "answers" && !printState.availableAnswers) ui.paper.append(node("p", "helper", "这些题没有答案或解析，请改选题目卷。"));
    ui.paper.append(node("p", "print-footer", `共 ${number} 题`));
    ui.paper.querySelectorAll("img").forEach((image) => { image.loading = "eager"; });
    syncIndividualLayout();
    if (printState.missing.length) ui.paper.prepend(node("p", "print-incomplete-warning", "本卷尚未完整载入，有选题缺失。请返回组卷处理后再打印。"));
    preparePrintLayout();
    const flow = node("div", "print-flow exam-source");
    flow.style.setProperty("--exam-font-size", `${options.font_size}pt`);
    flow.dataset.answerSpace = options.answer_space;
    flow.dataset.document = options.document;
    flow.append(...Array.from(ui.paper.childNodes));
    ui.paper.append(flow);
    printState.layoutSource = flow;
    printState.layoutPromise = refreshPrintPages(flow);
  }

  function syncIndividualLayout() {
    const picker = $("printIndividualQuestion");
    const previous = picker.value;
    const questions = Array.from(ui.paper.querySelectorAll(".print-question"));
    printState.firstQuestion = questions[0]?.dataset.questionId;
    picker.replaceChildren();
    questions.forEach(question => {
      const choice = node("option", "", `第 ${parseInt(question.querySelector(".qb-number")?.textContent, 10)} 题`);
      choice.value = question.dataset.questionId; picker.append(choice);
    });
    if (questions.some(question => question.dataset.questionId === previous)) picker.value = previous;
    syncIndividualControls();
  }

  function syncIndividualControls() {
    const id = $("printIndividualQuestion").value;
    const item = printState.items.find(item => item.id === id);
    $("printIndividualOption").value = printState.optionOverrides[id] || "";
    $("printIndividualOption").disabled = printState.exporting || !item || item.content?.body_mode === "source_image" || (!Object.keys(item.content?.options || {}).length && !(item.content?.figures || []).some(figure => /^[A-E]$/.test(figure.slot)));
    $("printIndividualSpace").value = printState.answerSpaceOverrides?.[id] || "";
    $("printIndividualSpace").disabled = printState.exporting || !item || item.question_type !== "free_response"
      || $("printDocument").value === "answers" || ($("printDocument").value === "combined" && $("printAnswerLayout").value === "inline");
    $("printIndividualBreak").checked = printState.questionBreaks.includes(id);
    $("printIndividualBreak").disabled = printState.exporting || !item || id === printState.firstQuestion;
    $("printIndividualHint").textContent = item?.content?.body_mode === "source_image" ? "原图题保留卷面排版，可调整顺序和分页；转成文字后可重排选项。" : id === printState.firstQuestion ? "首题已在第一页，无需另起页。" : "只影响当前组卷，不改题库原题。";
    renderFigureControls(id);
  }

  // 一道题有几张题干配图，这里就列几张，顺序和预览里从上往下看到的一致。老师指着
  // 预览上的第一张图，这儿就得是第一张 —— 编号对不上，等于这个功能没用。
  function stemFiguresOf(id) {
    const item = printState.items.find(item => item.id === id);
    // 原图题（整页扫描）本来就是一个整体，改宽度没有意义，预览里也不走配图那一行。
    if (!item || item.content?.body_mode === "source_image") return [];
    return (item.content?.figures || []).filter(figure => figure.slot === "stem");
  }

  function figureRow(index, count) {
    const row = node("div", "print-figure-row");
    const line = node("div", "print-figure-line");
    line.append(node("span", "print-figure-name", `第 ${index + 1} 张图`));
    const slider = node("input", "print-figure-slider");
    slider.type = "range";
    Object.assign(slider, { min: FIGURE_WIDTH_MIN, max: FIGURE_WIDTH_MAX, step: 1 });
    slider.dataset.figureIndex = String(index);
    slider.title = "左右拖动就把这张图放大或缩小，右边的毫米数跟着变。最多铺满整张 A4 的正文宽度。";
    slider.setAttribute("aria-label", `第 ${index + 1} 张图的宽度`);
    const size = node("input", "print-figure-mm");
    size.type = "number";
    Object.assign(size, { min: FIGURE_WIDTH_MIN, max: FIGURE_WIDTH_MAX, step: 0.5 });
    size.dataset.figureIndex = String(index);
    size.title = "直接填这张图印在纸上的宽度，单位毫米。填多少，导出的 Word 和 PDF 上就是多少。";
    size.setAttribute("aria-label", `第 ${index + 1} 张图的宽度（毫米）`);
    const reset = node("button", "button button-quiet print-figure-reset", "还原");
    reset.type = "button";
    reset.dataset.figureReset = String(index);
    reset.title = "把这张图恢复成系统自动的大小，不影响别的设置。";
    line.append(slider, size, node("span", "print-figure-unit", "毫米"), reset);
    // 第 1 张图本来就在第一行的开头，让它「从下一行开始」没有意义 ——
    // 勾上什么也不会发生，而一个点了没反应的开关比没有更让人以为自己没设置好。
    const first = count < 2 || index === 0;
    const breaker = node("label", "library-toggle switch print-figure-break");
    const toggle = node("input", "print-figure-break-box");
    toggle.type = "checkbox";
    toggle.dataset.figureBreak = String(index);
    toggle.disabled = first;
    toggle.title = first ? "第一张图本来就在这一行的开头，不需要再换行。" : "勾上之后，这张图自己另起一行，后面的图排在它下面一行。";
    breaker.append(toggle, node("span", "switch-track print-figure-track"),
      node("span", "print-figure-break-label", first ? "第一张图本来就另起一行" : "这张图从下一行开始"));
    breaker.querySelector(".switch-track").setAttribute("aria-hidden", "true");
    row.append(line, breaker);
    return row;
  }

  // 滑块一开始停在「这张图现在到底多宽」，而不是从头到尾都用同一个中间值。预览
  // 已经把每一张图的宽度写成内联样式了（排版函数写死的），量回来就是毫米 —— 老师
  // 不用先猜一个数才知道往左还是往右拖。
  function printedFigureWidths(id) {
    // 一道题跨页时会被排版函数拆成两段（前半页一段、后半页一段），两段共用同一个
    // 题目编号。只看第一段的话，恰好是题干占满一页、配图被推到下一页的那种题 —— 量
    // 到的段落里一张图也没有，滑块只能退回一个猜的默认宽度。
    const images = Array.from(ui.paper.querySelectorAll(`.print-question[data-question-id="${CSS.escape(id)}"]`))
      .flatMap(block => Array.from(block.querySelectorAll(".qb-figures .qb-figure img")));
    return images.map(image => {
      // offsetWidth 是排版后的原始宽度；getBoundingClientRect 会把整张卷子的缩放
      // 一起算进来，量出来的是屏幕上的大小，不是印在纸上的大小。
      const millimetres = Math.round(image.offsetWidth * 25.4 / 96 * 10) / 10;
      return millimetres > 0 ? millimetres : 40;
    });
  }

  function renderFigureControls(id = $("printIndividualQuestion").value) {
    const figures = stemFiguresOf(id);
    const box = $("printFigureBox"), list = $("printFigureList");
    box.hidden = !figures.length;
    if (!figures.length) { list.replaceChildren(); return; }
    const setting = printState.figureOverrides[id] || {};
    const printed = printedFigureWidths(id);
    // 切到另一道题才重建。同一道题重排时只改数值 —— 重建会把老师正按着的滑块换掉，
    // 拖到一半手一松，滑块就跳回原处。
    if (list.dataset.questionId !== id || list.children.length !== figures.length) {
      list.dataset.questionId = id;
      list.replaceChildren(...figures.map((figure, index) => figureRow(index, figures.length)));
    }
    figures.forEach((figure, index) => {
      const row = list.children[index];
      const chosen = setting.width ? setting.width[String(index)] : undefined;
      const current = chosen ?? printed[index] ?? 40;
      const slider = row.querySelector(".print-figure-slider"), size = row.querySelector(".print-figure-mm");
      const reset = row.querySelector(".print-figure-reset"), breaker = row.querySelector(".print-figure-break-box");
      // 第 1 张图的换行开关是锁着的，别在导出中把「导出时禁用」顺手把它解锁了。
      breaker.disabled = printState.exporting || figures.length < 2 || index === 0;
      slider.disabled = size.disabled = reset.disabled = printState.exporting;
      if (document.activeElement !== size) size.value = chosen ? String(chosen) : "";
      slider.value = String(Math.min(FIGURE_WIDTH_MAX, Math.max(FIGURE_WIDTH_MIN, Math.round(current))));
      slider.title = chosen
        ? `第 ${index + 1} 张图现在是 ${chosen} 毫米。左右拖动可以改。`
        : `第 ${index + 1} 张图现在是 ${current} 毫米，由系统自动定的。左右拖动可以改。`;
      reset.hidden = chosen === undefined;
      breaker.checked = (setting.breaks || []).includes(index);
    });
    $("printFigureAlign").value = setting.align || "";
    $("printFigureAlign").disabled = printState.exporting;
  }

  function changeFigureOverride(id, change) {
    const next = change({ ...(printState.figureOverrides[id] || {}) });
    // 三个开关都回到默认，就把这道题的记录整个删掉：草稿里不留「改过又改回来」的痕迹。
    if (next && Object.keys(next).length) printState.figureOverrides[id] = next;
    else delete printState.figureOverrides[id];
    markDraftDirty();
    renderPrint(printState.items);
  }

  async function refreshPrintPages(flow) {
    const token = ++printState.layoutToken;
    printState.layoutPending = true;
    printState.layoutError = "";
    $("printPageStatus").textContent = "正在排版…";
    $("printLayoutWarnings").hidden = true;
    syncExportButtons();
    try {
      await waitForPrintAssets();
      if (token !== printState.layoutToken || !ui.sheet.open) return;
      if (typeof window.ExamLayout?.paginate !== "function") throw new Error("分页组件未载入，请刷新页面后重试");
      const layoutHost = node("div", "print-paper");
      const result = await window.ExamLayout.paginate(flow, { host: layoutHost, ...currentPrintOptions() });
      if (token !== printState.layoutToken || !ui.sheet.open) return;
      ui.paper.replaceChildren(...result.pages);
      ui.paper.dataset.pageCount = String(result.page_count);
      window.ExamLayout.scale(ui.paper);
      // 「太宽就挡住导出」必须排在排版之后算：data-exam-math-overflow 是分页时
      // fitFormulas 标上去的，排在前面读的是上一版的卷面 —— 第一次打开一张
      // 含有放不下的公式的卷子，按钮不会挡、提示也不出，印出来才发现被切了。
      preparePrintLayout();
      const knownTypes = new Set(["single_choice", "multiple_choice", "fill_blank", "true_false", "free_response"]);
      const seen = new Set();
      ui.paper.querySelectorAll(".print-question").forEach(question => {
        const id = question.dataset.questionId;
        if (seen.has(id)) return;
        seen.add(id);
        const item = printState.items.find(item => item.id === id);
        if (!item) return;
        const group = printState.items.filter(other => knownTypes.has(item.question_type) ? other.question_type === item.question_type : !knownTypes.has(other.question_type));
        const number = parseInt(question.querySelector(".qb-number")?.textContent, 10) || seen.size;
        question.append(printTools(printState.items, group, group.indexOf(item), number));
      });
      // 重渲染会换掉所有工具面板：新节点的 open 是在挂监听之前设的，收不到 toggle，
      // 这里补一次定位，否则改完选项重新排版后，展开着的那个又会掉回被盖住的状态。
      ui.paper.querySelectorAll(".print-question-tools[open]").forEach(placePrintTools);
      // 排好版才知道哪些字段真的溢出了。之前这个函数一次都没被调用过，
      // 屏幕上那条 .print-paper [data-print-overflow] 规则因此永远不生效：
      // 太宽的公式直接冲出纸边，既不能滚也进不了键盘 Tab。
      requestAnimationFrame(() => updateOverflowHints());
      applyPrintFocus();
      $("printPageStatus").textContent = `A4 · 共 ${result.page_count} 页 · ${printState.items.length} 题`;
      $("printLayoutWarnings").textContent = result.warnings.join(" ");
      $("printLayoutWarnings").hidden = !result.warnings.length;
    } catch (error) {
      if (token !== printState.layoutToken) return;
      printState.layoutError = error.message || "排版失败，请重试";
      $("printPageStatus").textContent = printState.layoutError;
    } finally {
      if (token === printState.layoutToken) { printState.layoutPending = false; syncExportButtons(); }
    }
  }

  // Width fitting happens in the shared A4 paginator for both preview and PDF.
  // This UI check only reports its result; it must not rescale a paginated page.
  function preparePrintLayout() {
    const overflow = [...ui.paper.querySelectorAll('.katex[data-exam-math-overflow="1"]')];
    // The application's own export refuses to run while a formula cannot fit A4.
    // The browser's own print shortcut cannot be refused, and the formula is then
    // cut at the paper edge — 80 terms went in, 32 came out. Print the LaTeX source
    // for those formulas instead: it wraps, so no term is silently dropped.
    ui.paper.querySelectorAll(".print-formula-fallback").forEach(item => item.remove());
    for (const math of overflow) {
      const span = math.closest(".qb-math");
      const raw = span?.dataset.raw;
      if (!span || !raw) continue;
      const fallback = node("div", "print-formula-fallback");
      fallback.append(node("code", "", raw.replace(/^\$+|\$+$/g, "")));
      span.append(fallback);
    }
    const tooWide = overflow.length;
    const notice = $("printLayoutNotice");
    notice.textContent = tooWide ? `${tooWide} 处公式超出 A4 正文。可调整本次正文字号，或导出 Word 继续排版；题库内容不受影响。` : "";
    notice.hidden = !tooWide;
    printState.tooWide = tooWide;
    syncExportButtons();
  }

  function updateOverflowHints() {
    ui.paper.querySelectorAll(".print-overflow-hint").forEach((hint) => hint.remove());
    // The field stays focusable and scrollable whatever the preference says;
    // only the sentence and the description pointing at it are dropped, so no
    // invisible aria-describedby target is left behind.
    ui.paper.querySelectorAll('[data-print-overflow="1"]').forEach((field) => {
      field.removeAttribute("tabindex");
      field.removeAttribute("aria-describedby");
      delete field.dataset.printOverflow;
    });
    if (hintHidden("reading-overflow")) {
      // Still make a genuinely wide formula reachable by keyboard; only the
      // sentence and its description target go away.
      ui.paper.querySelectorAll(".qb-stem-body, .qb-option-body, .qb-analysis").forEach((field) => {
        if (field.scrollWidth <= field.clientWidth + 2) return;
        field.tabIndex = 0;
        field.dataset.printOverflow = "1";
        field.setAttribute("aria-label", "题目内容，可左右滚动查看完整公式");
      });
      return;
    }
    ui.paper.querySelectorAll(".qb-stem-body, .qb-option-body, .qb-analysis").forEach((field) => {
      if (field.scrollWidth <= field.clientWidth + 2) return;
      const hint = node("span", "print-overflow-hint no-print", "左右滑动查看完整公式；键盘可用左右方向键。");
      hint.id = `overflow-hint-${ui.paper.querySelectorAll(".print-overflow-hint").length}`;
      field.tabIndex = 0;
      field.dataset.printOverflow = "1";
      field.setAttribute("aria-describedby", hint.id);
      field.after(hint);
    });
  }

  function closePrintTools({ restoreFocus = false } = {}) {
    const opened = [...ui.paper.querySelectorAll(".print-question-tools[open]")];
    const summary = opened[0]?.querySelector("summary");
    opened.forEach(tools => { tools.open = false; });
    // Clear immediately, before a queued details toggle or layout redraw can
    // restore the dismissed menu from activeToolId.
    printState.activeToolId = null;
    if (restoreFocus) summary?.focus({ preventScroll: true });
    return opened.length > 0;
  }

  function dismissPrintToolsOutside(event) {
    if (ui.sheet.hidden || (event.button != null && event.button !== 0)) return;
    const opened = [...ui.paper.querySelectorAll(".print-question-tools[open]")];
    if (!opened.length || opened.some(tools => tools.contains(event.target))) return;
    closePrintTools();
    // Do not consume the click or move focus: the clicked input, export
    // action or another question's summary must still work normally.
  }

  function handlePrintEscape(event) {
    if (event.key !== "Escape" || ui.sheet.hidden || event.defaultPrevented
      || event.isComposing || event.keyCode === 229
      || document.querySelector("dialog[open]:not(#printSheet)")) return;
    event.preventDefault();
    event.stopPropagation();
    if (!closePrintTools({ restoreFocus: true })) closePrint();
  }

  document.addEventListener("pointerdown", dismissPrintToolsOutside);

  // Move up / down within the same section, or take the question out of the
  // basket, without leaving the preview.  Hidden when printing.
  function printTools(items, group, position, number) {
    const tools = node("details", "print-question-tools no-print");
    const toggle = node("summary", "", "调整");
    toggle.setAttribute("aria-label", `第 ${number} 题排版操作`);
    tools.append(toggle);
    const actions = node("div", "print-question-actions");
    actions.setAttribute("role", "group");
    actions.setAttribute("aria-label", `第 ${number} 题排序与排版`);
    const item = group[position];
    tools.open = printState.activeToolId === item.id;
    tools.addEventListener("toggle", () => {
      if (!tools.isConnected) return;
      if (tools.open) {
        printState.activeToolId = item.id;
        ui.paper.querySelectorAll(".print-question-tools[open]").forEach(other => { if (other !== tools) other.open = false; });
        placePrintTools(tools);
      } else {
        if (printState.activeToolId === item.id) printState.activeToolId = null;
        tools.classList.remove("opens-up");
        tools.closest(".exam-page")?.classList.remove("tools-open");
      }
    });
    const move = (step) => {
      const other = group[position + step];
      if (!other) return;
      const from = state.basket.indexOf(item.id);
      const to = state.basket.indexOf(other.id);
      if (from < 0 || to < 0) return;
      state.basket = workspace.moveWithinGroup(state.basket, item.id, other.id);
      saveBasket();
      const a = items.indexOf(item);
      const b = items.indexOf(other);
      [items[a], items[b]] = [items[b], items[a]];
      renderPrint(items);
      // 焦点等分页排完再给（applyPrintFocus），这里同步给的会被重排换掉。
      printState.printFocus = { id: item.id, action: step < 0 ? "up" : "down" };
    };
    const up = node("button", "", "↑ 上移");
    up.type = "button";
    up.title = "在本题型内上移";
    up.setAttribute("aria-label", `第 ${number} 题上移`);
    up.dataset.action = "up";
    up.disabled = printState.exporting || position === 0;
    up.addEventListener("click", () => move(-1));
    const down = node("button", "", "↓ 下移");
    down.type = "button";
    down.title = "在本题型内下移";
    down.setAttribute("aria-label", `第 ${number} 题下移`);
    down.dataset.action = "down";
    down.disabled = printState.exporting || position === group.length - 1;
    down.addEventListener("click", () => move(1));
    const remove = node("button", "remove", "移出");
    remove.type = "button";
    remove.disabled = printState.exporting;
    remove.title = "移出试题篮";
    remove.setAttribute("aria-label", `第 ${number} 题移出试题篮`);
    remove.addEventListener("click", () => {
      state.basket = state.basket.filter((id) => id !== item.id);
      saveBasket();
      items.splice(items.indexOf(item), 1);
      renderPrint(items);
      render();
      // 这道题没了，焦点落到顶上来的那道（原来那个位置）；同样等分页排完再给。
      printState.printFocus = { index: number - 1 };
    });
    const optionLayout = node("select", "print-single-layout");
    optionLayout.setAttribute("aria-label", `第 ${number} 题选项排版`);
    [["", "跟随全卷"], ["auto", "自动排版"], ["four", "一行四个"], ["two", "两行两个"], ["vertical", "四个选项竖排"]].forEach(([value, label]) => {
      const choice = node("option", "", label); choice.value = value; optionLayout.append(choice);
    });
    optionLayout.value = printState.optionOverrides[item.id] || "";
    optionLayout.disabled = printState.exporting;
    optionLayout.hidden = item.content?.body_mode === "source_image" || (!Object.keys(item.content?.options || {}).length && !(item.content?.figures || []).some(figure => /^[A-E]$/.test(figure.slot)));
    optionLayout.addEventListener("change", () => {
      if (optionLayout.value) printState.optionOverrides[item.id] = optionLayout.value;
      else delete printState.optionOverrides[item.id];
      markDraftDirty(); renderPrint(items);
    });
    const answerSpace = node("select", "print-single-space");
    answerSpace.setAttribute("aria-label", `第 ${number} 题解答留白`);
    [["", "留白跟随全卷"], ["none", "不留白"], ["small", "少量（12 毫米）"], ["medium", "适中（30 毫米）"], ["large", "较多（60 毫米）"]].forEach(([value, label]) => {
      const choice = node("option", "", label); choice.value = value; answerSpace.append(choice);
    });
    answerSpace.value = printState.answerSpaceOverrides[item.id] || "";
    answerSpace.hidden = item.question_type !== "free_response";
    answerSpace.disabled = printState.exporting || $("printDocument").value === "answers"
      || ($("printDocument").value === "combined" && $("printAnswerLayout").value === "inline");
    answerSpace.addEventListener("change", () => {
      if (answerSpace.value) printState.answerSpaceOverrides[item.id] = answerSpace.value;
      else delete printState.answerSpaceOverrides[item.id];
      markDraftDirty(); renderPrint(items);
    });
    const pageBreak = node("button", "", printState.questionBreaks.includes(item.id) ? "取消另起页" : "另起一页");
    pageBreak.type = "button"; pageBreak.disabled = printState.exporting || item.id === printState.firstQuestion;
    pageBreak.setAttribute("aria-label", `第 ${number} 题另起一页`);
    pageBreak.setAttribute("aria-pressed", String(printState.questionBreaks.includes(item.id)));
    pageBreak.addEventListener("click", () => {
      printState.questionBreaks = printState.questionBreaks.includes(item.id) ? printState.questionBreaks.filter(id => id !== item.id) : [...printState.questionBreaks, item.id];
      markDraftDirty(); renderPrint(items);
    });
    const editAnswer = node("button", "", "答案解析"); editAnswer.type = "button";
    editAnswer.disabled = printState.exporting;
    editAnswer.setAttribute("aria-label", `第 ${number} 题编辑答案解析`);
    editAnswer.addEventListener("click", () => openAnswerEditor(printState.items.map(value => ({ ...value, exam_number: groupedQuestionNumber(value) })), { scope: "paper", focus: item.id }));
    actions.append(up, down, remove, optionLayout, answerSpace, pageBreak, editAnswer);
    tools.append(actions);
    return tools;
  }

  // 1.13.3: 决定「调整」菜单往哪边展开，并把整张纸抬到其它纸之上。
  // 纸被 ExamLayout.scale() 套了 transform: scale()，自己就是一个层叠上下文，
  // 菜单的 z-index 出不来这张纸（详见 library.css 里 .exam-page.tools-open）。
  // 菜单原本只往下展开，题排在纸的下部就出纸、被后面那张纸整块盖死、点不动。
  // 量的时候只用 getBoundingClientRect：它给的是缩放后的值，同一坐标系可比；
  // offsetHeight 不受 transform 影响，拿来比会差一个缩放系数。
  function placePrintTools(tools) {
    const page = tools.closest(".exam-page");
    const actions = tools.querySelector(".print-question-actions");
    tools.classList.remove("opens-up");
    page?.classList.add("tools-open");
    if (!page || !actions) return;
    const paper = page.getBoundingClientRect();
    const limit = Math.min(paper.bottom, window.innerHeight);
    const down = actions.getBoundingClientRect();
    if (down.top >= Math.max(paper.top, 0) && down.bottom <= limit) return;
    // 下面放不下，试上面。
    tools.classList.add("opens-up");
    const up = actions.getBoundingClientRect();
    if (up.top >= Math.max(paper.top, 0) && up.bottom <= limit) return;
    // 两边都放不下：留纸内更多的那一边，别整个掉到纸外。
    const inPaperDown = Math.min(down.bottom, paper.bottom) - Math.max(down.top, paper.top);
    const inPaperUp = Math.min(up.bottom, paper.bottom) - Math.max(up.top, paper.top);
    if (inPaperDown > inPaperUp) tools.classList.remove("opens-up");
  }

  async function waitForPrintAssets() {
    let timer;
    try {
      const ready = Promise.all([
        document.fonts?.ready || Promise.resolve(),
        ...Array.from(ui.paper.querySelectorAll("img"), async (image) => {
          // Browsing uses lazy figures; offscreen questions must load before output.
          image.loading = "eager";
          // decode rejects for missing figures instead of exporting an incomplete paper.
          if (typeof image.decode === "function") await image.decode();
          else if (!image.complete) await new Promise((resolve, reject) => {
            image.addEventListener("load", resolve, { once: true });
            image.addEventListener("error", () => reject(new Error("试卷配图未能载入，请重试")), { once: true });
          });
          if (!image.naturalWidth) throw new Error("试卷配图未能载入，请重试");
        })
      ]);
      await Promise.race([ready, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("字体或配图仍未载入，请稍后重试")), 15000); })]);
    } catch (error) {
      throw new Error(error.message?.includes("字体") ? error.message : "试卷配图未能载入，请重试");
    } finally { clearTimeout(timer); }
  }

  function setExportBusy(busy) {
    if (busy) {
      printState.disabledControls = Array.from(ui.sheet.querySelectorAll(".print-options input, .print-options select, #closePrint, #clearBasket, #saveDraft, #saveDraftAs"))
        .map((control) => [control, control.disabled]);
      printState.disabledControls.forEach(([control]) => { control.disabled = true; });
    } else {
      (printState.disabledControls || []).forEach(([control, disabled]) => { control.disabled = disabled; });
      printState.disabledControls = [];
    }
    syncExportButtons();
  }

  // 导出的结果是一张留下来的卡片，不是闪一下就没的小字：说清文件叫什么、几道题、
  // 存在哪，而且能一键打开 —— 存到指定文件夹时后台会回一张凭据，前端以前校验完就扔了。
  function renderExportStatus({ text, path = "", tone = "", actions = [] }) {
    const status = $("printExportStatus");
    status.replaceChildren();
    status.className = `print-export-status no-print${tone ? ` is-${tone}` : ""}`;
    status.hidden = false;
    if (text) status.append(node("strong", "", text));
    if (path) status.append(node("span", "print-export-path", path));
    if (actions.length) {
      const row = node("div", "print-export-actions");
      for (const action of actions) row.append(action);
      status.append(row);
    }
  }

  function exportAction(label, title, run) {
    const button = node("button", "button button-small", label);
    button.type = "button"; button.title = title;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try { await run(); }
      catch (error) { toast(error.message || "打不开这个位置", "error"); }
      finally { button.disabled = false; }
    });
    return button;
  }

  async function openExported(target, fileToken) {
    // 凭据是一次性的（后台 pop 掉），所以「打开文件」用它；「打开文件夹」不带凭据，
    // 走已保存的导出位置，可以反复点。
    // 1.13：这里原来调的是一个本文件里根本不存在的 api() —— 点一下抛 ReferenceError，
    // 弹出来的是英文的 "api is not defined"，看上去就是「没反应」。后端、凭据、
    // 一次性令牌全都是好的，断的只有这一环。本文件所有其它请求都是直接 fetch
    // 加那两个头，照着写。
    const body = fileToken ? { target, file_token: fileToken } : { target };
    const response = await fetch("/api/export-preferences/open", {
      method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
      body: JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "打不开这个位置，请检查文件是否还在。");
    if (!result?.opened) throw new Error("这个位置打不开，请在文件夹里确认文件是否还在。");
  }

  async function exportPaper(format) {
    if (printState.exporting || state.draftSaving) return;
    if (!await checkMissingAnswers(format)) return;
    printState.exporting = true;
    setExportBusy(true);
    const status = $("printExportStatus");
    status.hidden = false;
    status.textContent = "正在检查选题、字体与配图…";
    try {
      // Recheck selected publication snapshots immediately before every output.
      await openPrint();
      if (printState.loading || printState.missing.length || !printState.items.length) throw new Error("选题尚未完整载入，请先处理未载入题目");
      if ((format === "split" || $("printDocument").value === "answers") && !printState.availableAnswers) throw new Error("这些题没有答案或解析，不能导出空答案卷");
      await waitForPrintAssets();
      await printState.layoutPromise;
      preparePrintLayout();
      const options = currentPrintOptions();
      if ((format === "print" || format === "pdf") && printState.layoutError) throw new Error(printState.layoutError);
      if ((format === "print" || format === "pdf") && printState.tooWide) throw new Error("有公式超出 A4 正文，请调整本次字号或导出 Word 继续排版");
      if (format === "print") {
        if (printState.tooWide) throw new Error("有公式超出 A4 正文，请调整本次字号或导出 Word 继续排版");
        status.textContent = "已准备好分页，正在打开打印窗口。";
        window.print();
      } else {
        if (typeof window.ExamExport?.download !== "function") throw new Error("导出组件未载入，请刷新页面后重试");
        status.textContent = format === "pdf" ? "正在生成 PDF 文件…" : "正在生成 Word 文件…";
        const result = await window.ExamExport.download(printState.items, { title: ui.printTitle.value.trim() || "练习", print_options: options, format,
          // 预览停在几页就报几页；后端排出来对不上就拒发，不让老师拿到不是他看过的那份。
          preview_page_count: Number(ui.paper.dataset.pageCount) || undefined,
          solutions: solutions.fixedSelections(printState.solutions, printState.items.map(item => item.id)) });
        const count = result.question_count || printState.items.length;
        const warning = result.warning ? `；${result.warning}` : "";
        if (result.saved) {
          renderExportStatus({
            text: `已导出 ${count} 道题${warning}`, path: result.path,
            actions: [
              exportAction("打开文件", `用默认程序打开 ${result.filename}`, () => openExported("file", result.file_token)),
              exportAction("打开文件夹", "打开导出文件夹", () => openExported("directory"))
            ]
          });
          toast("文件已保存到设置的文件夹", "success");
        } else {
          // 没配导出目录时文件走浏览器下载，页面确实不知道它落到哪儿 ——
          // 不编一个路径，只说清文件名，并把设置入口给出来。
          const link = node("a", "button button-small link-button", "设置导出位置");
          link.href = "/settings#display";
          renderExportStatus({ text: `已下载 ${count} 道题：${result.filename}${warning}`, actions: [link] });
          ui.sheet.scrollTo?.({ top: 0 });
          toast(`${format === "pdf" ? "PDF" : "Word"} 文件已下载`, "success");
        }
        if (result.warning) status.append(document.createTextNode(`　${result.warning}`));
      }
    } catch (error) {
      renderExportStatus({ text: error.message || "导出失败，请重试", tone: "error" });
      toast(error.message || "导出失败，请重试", "error");
    } finally {
      printState.exporting = false;
      setExportBusy(false);
      renderPrint(printState.items);
    }
  }

  function closePrint() {
    if (printState.exporting || state.draftSaving) { toast("请等待当前保存或导出完成", "error"); return; }
    ++printState.token;
    ++printState.layoutToken;
    printState.layoutPending = false;
    if (ui.sheet.open) ui.sheet.close();
    ui.sheet.hidden = true;
    document.body.classList.remove("printing");
    const target = printState.returnFocus;
    (target?.isConnected && !target.hidden ? target : ui.search).focus({ preventScroll: true });
  }

  let libraryNavigationPending = false, draftLeaveApproved = false;
  async function leaveLibraryFor(url) {
    if (libraryNavigationPending) return false;
    libraryNavigationPending = true;
    try {
      if (state.draftSaving || printState.exporting) { toast("请等待当前保存或导出完成", "error"); return false; }
      if (hasUnsavedDraft()) {
        const snapshot = draftSignature();
        if (!await confirmDialog({ title: "组卷还没保存", text: "本次组卷的设置或选题改动还没保存为草稿。离开后这些改动不会用于出卷；试题篮、题库和已保存的草稿保留。", ok: "放弃组卷并离开", danger: true, focusCancel: true })) return false;
        if (snapshot !== draftSignature()) { toast("组卷又有新改动，请先保存或重新确认离开", "error"); return false; }
      }
      if (state.draftSaving || printState.exporting) { toast("请等待当前保存或导出完成", "error"); return false; }
      // Bypass only this confirmed navigation's draft warning. Do not clear
      // the draft or disable other editors' genuine beforeunload protection.
      draftLeaveApproved = true;
      try { window.location.assign(url); }
      catch (error) { draftLeaveApproved = false; throw error; }
      return true;
    } finally { libraryNavigationPending = false; }
  }

  function handleLibraryNavigation(event) {
    const link = event.target.closest?.("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
      || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
    const url = new URL(link.href, window.location.href), here = new URL(window.location.href);
    if (url.origin !== here.origin) return;
    if (link.closest(".topnav") && link.getAttribute("aria-current") === "page" && url.pathname === here.pathname) { event.preventDefault(); return; }
    if (url.pathname === here.pathname && url.search === here.search && url.hash) return;
    if (!["/", "/library", "/settings"].includes(url.pathname) || (!hasUnsavedDraft() && !state.draftSaving && !printState.exporting)) return;
    event.preventDefault();
    void leaveLibraryFor(url.href);
  }

  function protectLibraryBeforeUnload(event) {
    if (draftLeaveApproved) { draftLeaveApproved = false; if (!state.draftSaving && !printState.exporting) return; }
    if (!hasUnsavedDraft() && !state.draftSaving && !printState.exporting) return;
    event.preventDefault(); event.returnValue = "";
  }

  // ---------------------------------------------------------------- 事件

  let searchTimer = null;
  ui.search.value = state.q;
  ui.search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => { state.q = ui.search.value; syncUrl(); load(); }, 250);
  });
  ui.source.addEventListener("change", () => { state.document = ui.source.value; syncUrl(); load(); });
  $("sortSelect").addEventListener("change", () => { state.sort = $("sortSelect").value; syncUrl(); load(); });
  // 篮的入口就是右边缘那条把手，导航抽屉（☰）里不再有试题篮：☰ 留给导航和工具。
  $("basketHandle").addEventListener("click", () => setBasketPanel(!state.basketVisible));
  $("allQuestionsButton").addEventListener("click", () => { state.view = "all"; render(); });
  $("basketViewButton").addEventListener("click", async () => { state.view = "selected"; render(); await refreshBasket({ force: true }); });
  $("selectVisible").addEventListener("change", () => {
    visibleItems().forEach((item) => { if ($("selectVisible").checked) state.selected.add(item.id); else state.selected.delete(item.id); });
    render();
  });
  $("clearSelection").addEventListener("click", () => { state.selected.clear(); render(); });
  $("withdrawSelected").addEventListener("click", () => withdrawSelected());
  $("generateSelectedTags").addEventListener("click", () => queueJobs("tags", { ids: [...state.selected] }));
  $("generateSelectedAnswers").addEventListener("click", () => queueJobs("answer", { ids: [...state.selected] }));
  $("addSelected").addEventListener("click", () => {
    if (new Set([...state.basket, ...state.selected]).size > 500) { toast("试题篮最多放 500 题，请先保存一份组卷", "error"); return; }
    state.basket = workspace.uniqueIds([...state.basket, ...state.selected]);
    state.selected.clear();
    saveBasket();
    render();
    toast(`试题篮共 ${state.basket.length} 题`, "success");
  });
  $("clearBasketPanel").addEventListener("click", async () => {
    if (!await confirmDialog({ title: "清空试题篮？", text: "只清空当前选题。题库和已保存的组卷草稿都保留。", ok: "清空试题篮" })) return;
    state.basket = []; saveBasket(); render();
  });
  $("closeQuestion").addEventListener("click", () => $("questionDialog").close());
  $("questionDialog").addEventListener("close", () => {
    const target = state.questionReturnFocus;
    if (target?.isConnected) target.focus({ preventScroll: true });
    else {
      const card = document.getElementById(`q-${state.questionReturnId}`);
      const button = card?.querySelector(".library-full-button");
      (button || ui.search).focus({ preventScroll: true });
    }
  });
  $("openDrafts").addEventListener("click", openDrafts);
  $("closeDrafts").addEventListener("click", () => $("draftsDialog").close());
  $("saveDraft").addEventListener("click", () => saveDraft());
  $("saveDraftAs").addEventListener("click", () => saveDraft({ copy: true }));
  document.addEventListener("library-ai-settings-saved", () => load({ quiet: true }));
  // 关掉「公式横向查看提示」之后，已经开着的卷子要立刻把那句话收掉。
  document.addEventListener("qb:hint-dismissed", (event) => {
    if (event?.detail?.category === "reading-overflow" && ui.sheet.open) updateOverflowHints();
  });
  ui.more.addEventListener("click", () => load({ append: true }));
  ui.basketButton.addEventListener("click", openPrint);
  $("printButton").addEventListener("click", () => exportPaper("print"));
  $("exportWord").addEventListener("click", () => exportPaper("docx"));
  $("exportPdf").addEventListener("click", () => exportPaper("pdf"));
  $("exportSplit").addEventListener("click", () => exportPaper("split"));
  $("managePrintAnswers").addEventListener("click", () => openAnswerEditor(printState.items.map(item => ({ ...item, exam_number: groupedQuestionNumber(item) })), { scope: "paper" }));
  $("printIndividualQuestion").addEventListener("change", syncIndividualControls);
  $("printIndividualOption").addEventListener("change", () => {
    const id = $("printIndividualQuestion").value, value = $("printIndividualOption").value;
    if (value) printState.optionOverrides[id] = value; else delete printState.optionOverrides[id];
    markDraftDirty(); renderPrint(printState.items);
  });
  $("printIndividualSpace").addEventListener("change", () => {
    const id = $("printIndividualQuestion").value, value = $("printIndividualSpace").value;
    if (value) printState.answerSpaceOverrides[id] = value; else delete printState.answerSpaceOverrides[id];
    markDraftDirty(); renderPrint(printState.items);
  });
  $("printIndividualBreak").addEventListener("change", () => {
    const id = $("printIndividualQuestion").value;
    printState.questionBreaks = $("printIndividualBreak").checked ? [...new Set([...printState.questionBreaks, id])] : printState.questionBreaks.filter(value => value !== id);
    markDraftDirty(); renderPrint(printState.items);
  });
  function without(setting, key) {
    const rest = { ...setting };
    delete rest[key];
    return rest;
  }
  $("printFigureAlign").addEventListener("change", () => {
    const id = $("printIndividualQuestion").value, align = $("printFigureAlign").value;
    changeFigureOverride(id, setting => (align ? { ...setting, align } : without(setting, "align")));
  });
  $("printFigureList").addEventListener("input", (event) => {
    const slider = event.target.closest(".print-figure-slider");
    if (!slider) return;
    // 拖的过程中只把旁边的毫米数跟上去。每一格都重排整张卷子的话，预览会一直闪，
    // 老师根本看不清自己拖到了哪儿；松手（change）才真的重排。
    const size = slider.parentElement.querySelector(".print-figure-mm");
    if (size && document.activeElement !== size) size.value = slider.value;
  });
  $("printFigureList").addEventListener("change", (event) => {
    const id = $("printIndividualQuestion").value;
    const slider = event.target.closest(".print-figure-slider");
    const size = event.target.closest(".print-figure-mm");
    const breaker = event.target.closest(".print-figure-break-box");
    if (slider) {
      const index = slider.dataset.figureIndex;
      changeFigureOverride(id, setting => ({ ...setting, width: { ...(setting.width || {}), [index]: Number(slider.value) } }));
    } else if (size) {
      const index = size.dataset.figureIndex, millimetres = Number(size.value);
      // 填了 0、填了 300、填成「宽一点」都不猜他要什么：退回这张图现在的样子，
      // 并在鼠标停上来的时候说明能填多少。
      if (!Number.isFinite(millimetres) || millimetres < FIGURE_WIDTH_MIN || millimetres > FIGURE_WIDTH_MAX) {
        size.value = printState.figureOverrides[id]?.width?.[index] ?? "";
        size.title = `请填 ${FIGURE_WIDTH_MIN}–${FIGURE_WIDTH_MAX} 之间的毫米数：太小图就没了，再大也超不过一张 A4 的正文宽度。`;
        return;
      }
      changeFigureOverride(id, setting => ({ ...setting, width: { ...(setting.width || {}), [index]: millimetres } }));
    } else if (breaker) {
      const index = Number(breaker.dataset.figureBreak);
      changeFigureOverride(id, (setting) => {
        const breaks = new Set(setting.breaks || []);
        if (breaker.checked) breaks.add(index); else breaks.delete(index);
        const kept = [...breaks].sort((a, b) => a - b);
        return kept.length ? { ...without(setting, "breaks"), breaks: kept } : without(setting, "breaks");
      });
    }
  });
  $("printFigureList").addEventListener("click", (event) => {
    const reset = event.target.closest(".print-figure-reset");
    if (!reset) return;
    const id = $("printIndividualQuestion").value, index = reset.dataset.figureReset;
    changeFigureOverride(id, (setting) => {
      const width = { ...(setting.width || {}) };
      delete width[index];
      return Object.keys(width).length ? { ...setting, width } : without(setting, "width");
    });
  });
  $("closePrint").addEventListener("click", closePrint);
  $("clearBasket").addEventListener("click", () => { state.basket = []; saveBasket(); closePrint(); render(); });
  let printTitleTimer;
  ui.printTitle.addEventListener("input", () => { markDraftDirty(); clearTimeout(printTitleTimer); printTitleTimer = setTimeout(() => renderPrint(printState.items), 160); });
  ui.printAnswers.addEventListener("change", () => { $("printDocument").value = ui.printAnswers.checked ? "combined" : "questions"; markDraftDirty(); renderPrint(printState.items); });
  [$("printDocument"), $("printAnswerLayout"), $("printPagination"), $("printOptionLayout"), $("printFontSize"), $("printAnswerSpace"), $("printStudentInfo"), ui.printOrigin, ui.printAi].forEach((control) => {
    control.addEventListener("change", () => { markDraftDirty(); $("printExportStatus").hidden = true; renderPrint(printState.items); });
  });
  ui.sheet.addEventListener("cancel", (event) => { event.preventDefault(); closePrint(); });
  ui.sheet.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const controls = Array.from(ui.sheet.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]'))
      .filter((element) => element.getClientRects().length && !element.closest("[hidden]"));
    const first = controls[0], last = controls[controls.length - 1];
    if (!first) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  // 缩放系数变了，同一页里「下面放不放得下」的答案也会变，展开着的菜单要重新定位。
  const rescalePrintPaper = () => {
    if (!ui.sheet.open) return;
    window.ExamLayout?.scale(ui.paper);
    ui.paper.querySelectorAll(".print-question-tools[open]").forEach(placePrintTools);
  };
  window.addEventListener("resize", rescalePrintPaper);
  $("printSettings").addEventListener("toggle", () => requestAnimationFrame(rescalePrintPaper));
  window.addEventListener("beforeunload", protectLibraryBeforeUnload);
  document.addEventListener("click", handleLibraryNavigation);
  ui.tagSelect.addEventListener("change", () => {
    const ids = Array.from(ui.tagSelect.selectedOptions).map((option) => option.value).filter(Boolean).join(",");
    state.tag = ""; state.tagAll = state.tagAny = state.tagNot = "";
    if (state.tagMode === "not") state.tagNot = ids;
    else if (state.tagMode === "any") state.tagAny = ids;
    else state.tagAll = ids;
    syncUrl(); load();
  });
  $("tagMatchMode")?.addEventListener("change", () => {
    const selected = Array.from(ui.tagSelect.selectedOptions).map((option) => option.value).filter(Boolean).join(",");
    state.tagMode = $("tagMatchMode").value; state.tag = ""; state.tagAll = state.tagAny = state.tagNot = "";
    if (state.tagMode === "not") state.tagNot = selected; else if (state.tagMode === "any") state.tagAny = selected; else state.tagAll = selected;
    syncUrl(); load();
  });
  $("saveTagFilter")?.addEventListener("click", () => {
    const filter = currentTagFilter();
    if (!filter.legacy && !filter.all && !filter.any && !filter.not) { toast("先选择一个或多个标签，再保存常用筛选。", "error"); return; }
    const name = window.prompt("给这个标签筛选起个名字"); if (!name || !name.trim()) return;
    const saved = savedTagFilters().filter((item) => item.name !== name.trim()); saved.push({ name: name.trim(), filter: filter });
    try { localStorage.setItem("qb-library-saved-tag-filters", JSON.stringify(saved.slice(-30))); renderSavedTagFilters(); toast("常用筛选已保存。", "success"); }
    catch (_) { toast("浏览器无法保存常用筛选。", "error"); }
  });
  $("tagSavedFilterSelect")?.addEventListener("change", () => {
    const index = Number($("tagSavedFilterSelect").value), saved = savedTagFilters();
    if (Number.isInteger(index) && saved[index]) applySavedTagFilter(saved[index].filter);
    $("deleteTagFilter").disabled = !$("tagSavedFilterSelect").value;
  });
  $("deleteTagFilter")?.addEventListener("click", () => {
    const index = Number($("tagSavedFilterSelect").value), saved = savedTagFilters();
    if (!Number.isInteger(index) || !saved[index]) return;
    saved.splice(index, 1); localStorage.setItem("qb-library-saved-tag-filters", JSON.stringify(saved)); renderSavedTagFilters();
  });
  ui.sourceDialog.addEventListener("click", (event) => {
    if (event.target === ui.sourceDialog || event.target.closest("[data-close]")) ui.sourceDialog.close();
  });
  ui.sourceDialog.addEventListener("close", () => { ++sourceState.token; stopSourcePan(); });
  ui.sourceQuestion.addEventListener("click", () => { sourceState.mode = "question"; sourceState.zoom = 1; renderSource(); });
  ui.sourceWholePage.addEventListener("click", () => { sourceState.mode = "page"; sourceState.zoom = 1; renderSource(); });
  ui.sourceZoomOut.addEventListener("click", () => zoomSource(sourceState.zoom - 0.5));
  ui.sourceZoomIn.addEventListener("click", () => zoomSource(sourceState.zoom + 0.5));
  ui.sourceFit.addEventListener("click", fitSourceWindow);
  ui.sourcePages.addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    if (!event.deltaY) return;
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? ui.sourcePages.clientHeight : 1);
    zoomSource(sourceState.zoom * Math.exp(-Math.max(-240, Math.min(240, delta)) * 0.002), event.clientX, event.clientY);
  }, { passive: false });
  ui.sourcePages.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.pointerType !== "mouse" || event.altKey || !sourceReady()) return;
    if (event.target.closest("a, button, input, textarea, select, summary, label")) return;
    const rect = ui.sourcePages.getBoundingClientRect();
    if (event.clientX >= rect.left + ui.sourcePages.clientWidth || event.clientY >= rect.top + ui.sourcePages.clientHeight) return;
    event.preventDefault();
    stopSourcePan();
    sourcePan = { pointerId: event.pointerId, x: event.clientX, y: event.clientY,
      left: ui.sourcePages.scrollLeft, top: ui.sourcePages.scrollTop };
    ui.sourcePages.setPointerCapture(event.pointerId);
    ui.sourcePages.classList.add("panning");
  });
  ui.sourcePages.addEventListener("pointermove", (event) => {
    if (!sourcePan || event.pointerId !== sourcePan.pointerId) return;
    if (!(event.buttons & 1)) { stopSourcePan(); return; }
    ui.sourcePages.scrollLeft = sourcePan.left - (event.clientX - sourcePan.x);
    ui.sourcePages.scrollTop = sourcePan.top - (event.clientY - sourcePan.y);
  });
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((type) => ui.sourcePages.addEventListener(type, stopSourcePan));
  ui.sourcePages.addEventListener("dragstart", (event) => event.preventDefault());
  window.addEventListener("blur", stopSourcePan);
  $("confirmDialog").addEventListener("click", (event) => {
    if (event.target === $("confirmDialog")) $("confirmDialog").close();
  });
  // 查找与筛选在左侧栏，窄屏（≤979px）改成右侧浮层。触发按钮在顶栏右端，
  // 和左端的导航抽屉分开，两个面板不会同时开着。
  const railToggle = $("libraryFilterToggle");
  const railScrim = $("libraryRailScrim");
  const setRail = (open) => {
    if (open) {
      window.QBSiteDrawer?.close?.();
      // 窄屏上侧栏是浮层，专注模式把它 display:none 掉了。要不然「宽屏进了专注、
      // 再把窗口拖窄」会卡在一个既看不见也打不开的侧栏上。
      if (document.body.classList.contains("library-focus-mode")) focusToggle?.set(false);
      // 窄屏上右侧已经有筛选浮层了，篮抽屉要让位：两个浮层不同时开。
      if (document.body.classList.contains("library-basket-open")) setBasketPanel(false);
      if (document.body.classList.contains("library-inspector-open")) setInspectorOpen(false);
    }
    document.body.classList.toggle("rail-open", open);
    railScrim.hidden = !open;
    railToggle.setAttribute("aria-expanded", String(open));
    if (open) ui.search?.focus({ preventScroll: true });
  };
  railToggle.addEventListener("click", () => setRail(!document.body.classList.contains("rail-open")));
  railScrim.addEventListener("click", () => setRail(false));
  $("inspectorToggle").addEventListener("click", () => setInspectorOpen(window.matchMedia("(max-width: 979px)").matches
    ? !document.body.classList.contains("library-inspector-open") : document.body.classList.contains("library-inspector-collapsed")));
  $("closeInspector").addEventListener("click", () => setInspectorOpen(false));
  $("inspectorScrim").addEventListener("click", () => setInspectorOpen(false));
  window.matchMedia("(max-width: 979px)").addEventListener("change", () => {
    document.body.classList.remove("library-inspector-open"); $("inspectorScrim").hidden = true;
    ui.inspectorToggle?.setAttribute("aria-expanded", String(!window.matchMedia("(max-width: 979px)").matches && !document.body.classList.contains("library-inspector-collapsed")));
  });
  $("libraryTagManager").addEventListener("click", openTagManager);
  $("editSelectedTags").addEventListener("click", () => openTagEditor(null));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !document.body.classList.contains("rail-open")) return;
    // 和抽屉同一规矩：原生 <dialog> 在 top layer，永远盖住侧栏，Esc 先让给它。
    if (document.querySelector("dialog[open]")) return;
    event.preventDefault();
    setRail(false);
    railToggle.focus({ preventScroll: true });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !document.body.classList.contains("library-inspector-open") || document.querySelector("dialog[open]")) return;
    event.preventDefault();
    setInspectorOpen(false);
    ui.inspectorToggle?.focus({ preventScroll: true });
  });
  $("libraryKeysButton").addEventListener("click", () => window.QBShortcutHelp?.open("library"));
  // 原来这里常驻两条提示条（题库快捷键、打印快捷键）外加一个「恢复操作提示」按钮。
  // 三样加起来是横在题目上方的一整条，眼睛得先扫过它才看得到题。提示内容都在
  // 设置里那一栏（快捷键按钮 / ? 键），这页不再挂常驻提示，也不再需要「恢复」——
  // 没有被这里关掉的东西，就没有东西要恢复。
  document.addEventListener("keydown", (event) => {
    const editable = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]';
    if (tagManagerDialog?.open) return;
    const ordinary = !event.defaultPrevented && !event.isComposing && event.keyCode !== 229 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.repeat
      && !event.target?.closest?.(editable) && !document.activeElement?.closest?.(editable);
    if (ordinary && !event.shiftKey && event.key === "/" && ui.sheet.hidden && document.activeElement !== ui.search && !document.querySelector("dialog[open]") && !ui.search.disabled) {
      event.preventDefault();
      ui.search.focus();
    }
    if (ordinary && event.key === "?" && ui.sheet.hidden && !document.querySelector("dialog[open]")) {
      event.preventDefault(); window.QBShortcutHelp?.open("library");
    }
    handlePrintEscape(event);
  });

  if (state.document) ui.source.value = state.document;
  saveBasket();
  load();
})();
