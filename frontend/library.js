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
    answerFilters: $("answerFilters"), tagBox: $("tagFilterBox"), tagSelect: $("tagSelect"), todos: $("libraryTodos")
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
    // 1.25.78：处理完一道 AI 建议后，自动滚到下一道仍有未处理建议的题。默认关 —
    // 老师想要在当前题上继续操作时保持原状；想要"清空 440 条建议"那种连点一遍
    // 一次性跑完的感觉，开起来。
    autoAdvanceSuggestion: params.get("auto_advance") === "true",
    sort: ["source", "number"].includes(params.get("sort")) ? params.get("sort") : "recent",
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
    // 题库里到底有没有 AI 审核的题 / 缺答案的题 / 打过标签的题。面板里一张卡片
    // 出不出现只看这个，不看当前筛选下的数字 —— 1.25.24 的原话是「我选择了两三个
    // 标签之后，这个界面就变了，格局就变了」。
    presence: { review_ai: 0, answer_missing: 0, tags: 0 },
    selectedTagNames: {},
    loading: false,
    token: 0,
    basket: loadBasket()
  };
  // 筛选刷新会重建这个区域，但展开状态应在当前浏览过程中保留。
  let todoMenuOpen = false;

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

  // 页面出问题时，屏幕上要写人能看懂的话。异常原文（"Failed to fetch"、
  // "Cannot read properties of null"）只进控制台给开发者看，不往老师脸上贴。
  function problemText(error) {
    if (error) console.error(error);
    const raw = String((error && error.message) || "");
    if (/failed to fetch|networkerror|load failed|network request/i.test(raw)) {
      return "连不上本机服务。请先确认题有据还开着，再点「重试读取」。";
    }
    return "题库这一页没能显示出来，你已有的题目、标签和答案都还在。"
      + "可以先点「重试读取」；还是一样的话，关掉题有据重新打开再试。";
  }

  // 提示条只做一件事：说清现在怎么了、下一步点哪里。
  function showLibraryProblem(message, retry) {
    const box = $("libraryLoadError");
    if (!box) return;
    const button = node("button", "button button-small", "重试读取");
    button.id = "retryLibraryLoad";
    button.type = "button";
    button.addEventListener("click", retry || (() => load()));
    box.replaceChildren(node("span", "", message), button);
    box.hidden = false;
    ui.more.hidden = true;
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
      ["untagged_knowledge", state.untaggedKnowledge ? "true" : ""], ["tag_review", state.tagReview ? "true" : ""],
      ["auto_advance", state.autoAdvanceSuggestion ? "true" : ""]]) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    if (state.sort === "source" || state.sort === "number") url.searchParams.set("sort", state.sort);
    else url.searchParams.delete("sort");
    url.searchParams.delete("focus");
    window.history.replaceState(null, "", url);
  }

  function libraryQuery(filters = state, { limit = 40, offset = 0 } = {}) {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset), sort: filters.sort || "recent" });
    const aliases = { tag_all: "tagAll", tag_any: "tagAny", tag_not: "tagNot", tag_mode: "tagMode", untagged_knowledge: "untaggedKnowledge", tag_review: "tagReview", auto_advance: "autoAdvanceSuggestion" };
    for (const key of ["q", "document", "type", "review", "answer", "tag", "tag_all", "tag_any", "tag_not", "tag_mode", "untagged_knowledge", "tag_review", "auto_advance"]) {
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
          state.ai = { mode: settings.mode, provider: settings.provider, message: settings.message,
                       api_ready: settings.api_ready, key_configured: settings.key_configured,
                       configured: settings.configured, status: settings.status };
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
      state.presence = body.presence || state.presence;
      state.selectedTagNames = body.selected_tag_names || {};
      state.tagWork = body.tag_work || state.tagWork;
      state.features = body.features || {};
      state.ai = body.ai || { mode: "assistant" };
      state.items = append ? state.items.concat(body.items) : body.items;
      body.items.forEach(item => state.retainedTagItems.delete(item.id));
      // 1.25.43：从核对页点「在题库中看」时带来的 focus，可能不在这 40 道里。
      // 单独取回这一道放在最前面 —— 否则人只看到一页无关的题，以为按钮没反应。
      if (!append && !quiet && state.focus && !state.items.some(item => item.id === state.focus)) {
        try {
          const pinned = await fetch(`/api/library?ids=${encodeURIComponent(state.focus)}&limit=1`, { cache: "no-store" });
          const pinnedBody = await pinned.json();
          const item = (pinnedBody.items || [])[0];
          if (pinned.ok && item && token === state.token) state.items = [item, ...state.items];
        } catch { /* 取不回来就按普通列表显示，不把整页拖垮 */ }
      }
      markLibraryResult(query);
      state.items.forEach((item) => state.catalog.set(item.id, item));
      try {
        render();
      } catch (error) {
        // 画界面的错和读数据的错是两件事：不能把「Cannot read properties…」当成
        // 「读取失败」写进提示条，也不能让整页停在空白。
        setStatus("这一页没能显示出来。", "warn");
        showLibraryProblem(problemText(error), () => load());
        return;
      }
      // 助手任务只等待工具写回；API 任务由后台处理。两种结果都刷新显示。
      scheduleJobRefresh();
    } catch (error) {
      if (token === state.token) {
        setStatus(state.items.length ? "当前显示上一次读取的结果，尚未应用这次筛选。" : "题库尚未读取成功。", "warn");
        showLibraryProblem(problemText(error), () => load({ append, quiet }));
      }
    } finally {
      if (token === state.token) state.loading = false;
    }
  }

  function pendingJobItems() {
    return visibleItems().filter((item) => (item.job_details || [])
      .some((job) => (job.kind === "tags" ? true : state.features.ai_answer)));
  }

  // 1.25.31：后台刷新只读「有任务的那几道题」。
  // 以前每 4 秒把屏上显示的题整屏重读一遍（一次 100 道，显示 400 道就是 4 次请求、
  // 约 1.3 秒），AI 一跑起来页面就一直在这上面打转。现在按编号只取那几行，
  // 合并回列表；等最后一道也做完时，再做一次完整刷新，把「还有活没干」的数字对上。
  async function refreshPendingJobs() {
    const pending = pendingJobItems();
    if (!pending.length) { scheduleJobRefresh(); return; }
    const ids = pending.slice(0, 100).map((item) => item.id).join(",");
    try {
      const response = await fetch(`/api/library?ids=${encodeURIComponent(ids)}&limit=100`, { cache: "no-store" });
      if (response.ok) {
        const body = await response.json();
        const fresh = new Map((body.items || []).map((item) => [item.id, item]));
        let changed = false;
        state.items = state.items.map((item) => {
          const next = fresh.get(item.id);
          if (!next) return item;
          changed = true;
          return next;
        });
        if (changed) render();
      }
    } catch (_) { /* 网络抖一下：等下一轮，不清空已有内容 */ }
    // 最后一道也做完了：完整刷新一次，把「还有活没干」的数字和排队状态对上。
    if (!pendingJobItems().length) { load({ quiet: true }); return; }
    scheduleJobRefresh();
  }

  function scheduleJobRefresh() {
    const pending = pendingJobItems();
    if (!pending.length) return;
    state.poll = window.setTimeout(refreshPendingJobs,
      pending.some((item) => (item.job_details || []).some((job) => job.executor === "api")) ? 4000 : 10000);
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
    // 一张卡片出不出现，看的是题库里有没有这类数据（state.presence），不是当前筛选
    // 下还剩多少。原来在这里用 emptied 兜底：别的筛选把结果清空时把整组顶出来，
    // 而那一维在题库里本来就一道都没有 —— 面板于是重排。老师选两三个标签、
    // 筛到 0 道题就能看见「谁审核的」凭空冒出来。1.25.24 修。
    // 当前正选着的那一维永远留着，不然筛着筛着把自己的控件筛没了。
    renderReviewFilters(facets.reviews || {});
    renderAnswerFilters(facets.answers || {});
    renderTagFilter(facets.tags || []);
    renderTodos();
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
    // 1.25.20：这两个筛选入口搬进了待办折叠区，class 也跟着换了。锁不锁它们
    // 仍然是「已选题」视图的事 —— 只锁上面那批会把它们漏掉，筛选在这个视图里
    // 一点都点不动。
    for (const control of document.querySelectorAll("#libraryTodos .library-todo-look button")) control.disabled = selectedView;
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
    // 只管真正会排队的那些。这个选择器能不能只圈到 AI 动作，取决于筛选组那一个
    // div 挂没挂 library-tool-actions —— 1.25.20 两边都挂了，于是这里把两个免费的
    // 筛选也一起锁掉，AI 一登记它们就变灰；1.25.21 把筛选组挪回自己的 class。
    // 改这两处时必须一起验：把 class 加回去，AI 排队时那两个筛选还点不动。
    for (const control of document.querySelectorAll("#libraryTodos .library-tool-actions button")) {
      control.disabled = queueBusy || selectedView || control.dataset.disabledInSelectedView === "true";
      // 排队期间要改名的只有刚才被点的那一个；别的按钮保持自己的名字，
      // 只是被锁住 —— 让它们也跟着说「正在排队」是在骗用户，以为他排了两队。
      if (queueBusy && control.dataset.queueAction === queueAction) markQueueing(control);
      else unmarkQueueing(control);
    }
  }

  // 有没有答案：挑出原卷没给答案的题（补答案或做 AI 参考答案时用）。
  // 这张卡片看不看题库有没有缺答案的题（state.presence），不看当前筛选下还剩几道
  // 没答案的 —— 后者一筛到 0 卡片就会消失，下面的全顶上去，格局跟着变。
  function renderAnswerFilters(answers) {
    const box = ui.answerFilters;
    const yes = Number(answers.yes) || 0;
    const no = Number(answers.no) || 0;
    box.hidden = !Number(state.presence?.answer_missing) && !state.answer;
    box.replaceChildren();
    if (box.hidden) return;
    [["", "全部", yes + no], ["yes", "有答案", yes], ["no", "无答案", no]].forEach(([key, label, count]) => {
      const button = node("button", `draft-filter${state.answer === key ? " active" : ""}`);
      button.append(node("span", "", label), node("span", "count", count));
      button.type = "button";
      // 1.25.74：「有答案」单看名字容易误解成"老师给过答案没给过"，其实它是「题库里已经
      // 保存了答案解析」 — 上传和切题不会自动写答案，所以新题进来都是 0；不解释就会一直奇怪。
      button.title = key === "yes" ? "题库里保存了答案解析的题"
        : key === "no" ? "题库里还没写答案的题 — 上传和切题都不会自动加答案"
        : "全部";
      button.setAttribute("aria-pressed", String(state.answer === key));
      if (!count && state.answer !== key) {
        button.dataset.emptyFacet = "1";
        button.title = button.title + "（当前筛选下没有）";
      }
      button.addEventListener("click", () => { state.answer = key; syncUrl(); load(); });
      box.append(button);
    });
  }

  // 1.25.27：标签筛选用一排带勾选框的行，不再用系统的多选列表框。
  // 原来那个 <select multiple> 要点一下选中、按住 Ctrl 才能多选，鼠标划过还会留下一条
  // 深色高亮 —— 老师看到的就是一个「灰盒子，点一下题目就少了一半」。
  // 勾选框的好处是：能多选、看得见选了哪几个、取消就是再点一下。
  function tagChoice(id, text, checked, tag) {
    const row = node("label", "library-tag-choice");
    const box = node("input", "library-tag-choice-box");
    box.type = "checkbox";
    box.value = id;
    box.checked = Boolean(checked);
    row.append(box);
    // 1.25.93：标签前面加一颗彩色圆点 —— 跟教材目录、标签管理、标签选择窗口里那颗是
    // 同一个色、同一个样子。筛选用的是同一批标签，不该又是一个长相。
    if (tag) {
      const dot = node("span", "library-tag-choice-dot");
      const color = workspace.tagColor(tag, tagCatalogue.tags || directory.tags || []);
      if (color) { dot.style.setProperty("--tag-color", color); row.append(dot); }
    }
    row.append(node("span", "library-tag-choice-name", text));
    return row;
  }

  // 1.25.93：筛选框里那一颗标签要显示自己的颜色，得先找到这颗标签。
  function knownTag(id) {
    const key = String(id);
    return (tagCatalogue.tags || []).find(tag => String(tag.id) === key)
      || (directory.tags || []).find(tag => String(tag.id) === key) || null;
  }

  function selectedTagIds() {
    return [...ui.tagSelect.querySelectorAll('input[type="checkbox"]:checked')]
      .map((box) => box.value).filter(Boolean);
  }

  function renderTagFilter(tags) {
    // 题库里打过正式标签就一直有这张卡；当前筛选下筛不到标签了，
    // 那是「这里没有」的问题，不是「这一维不存在」—— 用一行说明讲清楚，不收卡片。
    ui.tagBox.hidden = !(tags.length || Number(state.presence?.tags)
      || state.tagAll || state.tagAny || state.tagNot || state.tag);
    if (ui.tagBox.hidden) return;
    const selected = String(state.tagMode === "not" ? state.tagNot : state.tagMode === "any" ? state.tagAny : state.tagAll).split(",").filter(Boolean);
    // 勾一个标签就要重新读一次题库，列表跟着重建；不记住滚动位置的话，
    // 勾到列表靠下的标签就会跳回顶部，再想勾旁边那个又得往下翻一次。
    const scrollTop = ui.tagSelect.scrollTop;
    // 用键盘勾选的人，重建列表后焦点会掉回页面顶部；记住是哪一个，建完再还回去。
    const focused = ui.tagSelect.contains(document.activeElement) ? document.activeElement.value : null;
    ui.tagSelect.replaceChildren();
    if (!tags.length && !selected.length && !state.tag) {
      ui.tagSelect.append(node("p", "library-tag-empty", "当前筛选下没有可用的标签"));
    }
    const shown = new Set();
    tags.forEach((item) => {
      shown.add(item.id);
      ui.tagSelect.append(tagChoice(item.id, `${item.tag}（${item.count}）`, selected.includes(item.id), knownTag(item.id)));
    });
    // 筛到 0 道题时，可选列表里一个标签都不剩，但老师手上那几个还得看得见、还得能取消。
    // 显示标签的名字；名字也查不到（标签被删了）才退回编号，并且说清这是什么。
    selected.forEach((id) => {
      if (shown.has(id)) return;
      const known = state.selectedTagNames?.[id];
      ui.tagSelect.append(tagChoice(id, known || `标签已被删除 · ${id.slice(0, 8)}`, true));
    });
    if (state.tag) ui.tagSelect.append(tagChoice("", state.tag, true));
    ui.tagSelect.scrollTop = scrollTop;
    if (focused !== null) {
      const again = [...ui.tagSelect.querySelectorAll('input[type="checkbox"]')]
        .find((box) => box.value === focused);
      if (again) again.focus({ preventScroll: true });
    }
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
    const current = select.value; select.replaceChildren(new Option("选择一条", ""));
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

  // 1.25.20：三样「还有活没干」合成一处，跟着题目列表那一行走，筛选行只留来源和更多筛选。
  function renderTodos() {
    const box = ui.todos;
    box.replaceChildren();
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
    // 1.25.23：原来只有一组 batchActions，五颗按钮平铺在一列里。老师说这些按钮
    // 「无非分成两类，一个处理答案，一个处理标签」—— 是的，按"它动的是什么"分开才
    // 看得出差别，因为平铺时「补齐缺的参考答案」和「补齐缺的知识点标签」长得一样，
    // 而「只处理当前这批 · 807 题」根本看不出它动的是标签。
    const tagActions = [];      // 标签这一类：两个筛选 + 两个 AI 动作
    const answerActions = [];   // 答案这一类：一个 AI 动作
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
      // 1.25.78："处理完自动跳下一条"开关在 activeFilters 那一行画，不藏在
      // libraryTodos 下拉里，老师不用展开就能看见。
    }
    if (missingCount || state.untaggedKnowledge) {
      const untagged = iconButton("button", `button button-quiet button-small library-tool-action${state.untaggedKnowledge ? " active" : ""}`,
        `未标知识点 · ${missingCount} 题`);
      untagged.title = `整个题库里还没打上正式知识点标签的 ${missingCount} 道题。点一下只看这些题。`;
      untagged.setAttribute("aria-pressed", String(state.untaggedKnowledge));
      untagged.addEventListener("click", () => { state.untaggedKnowledge = !state.untaggedKnowledge; syncUrl(); load(); });
      quickActions.push(untagged);
      // 1.25.54：贴着这个小按钮进「逐题贴」流水线 —— 先让标签库有内容，再谈目录怎么翻。
      if (missingCount && tagsEnabled) {
        const flow = node("button", "button button-small library-tool-action tag-flow-start", "逐题贴");
        flow.type = "button";
        flow.title = `一次只显示一道题：输入标签名、回车贴上，自动跳到下一题；空格跳过。`
          + `整个题库还有 ${missingCount} 道题没标知识点。`;
        flow.addEventListener("click", () => void openTagFlow());
        quickActions.push(flow);
      }
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
      // 1.25.23：标了 data-cost 的按钮，界面上会多一枚「AI」小标 —— 花钱这件事写在
      // 按钮自己身上，而不是只写在面板顶上那一行说明里（说明只写一次，扫过去容易漏）。
      tags.dataset.cost = "ai";
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
      tagActions.push(tags);
      const current = iconButton("button", "button button-quiet button-small library-tool-action",
        `只处理当前这批 · ${Number(state.total) || 0} 题`, "plus");
      // 1.25.21：原来只写「每道题调用一次 AI，会消耗额度」。没筛任何东西时这一下就是
      // 807 次调用 —— 代价没写成数字，看着像个小动作。把次数直接说出来。
      current.title = apiReady
        ? `只处理你现在筛选出来的这 ${Number(state.total) || 0} 道题，包括还没加载到屏幕上的。`
          + `点下去会调用 AI ${Number(state.total) || 0} 次（每道题一次），会消耗额度。`
          + "想补整个题库里还没提过建议的题，请用上面那条，那条只排真正没排过队的。"
          + "生成的标签只是建议，要你逐条核对确认后才贴到题目上。"
        : "需要先到「设置 → API 配置」里配置并测试通过。";
      current.dataset.disabledInSelectedView = String(!apiReady || Number(state.total) === 0);
      current.dataset.queueAction = "tags-current";
      current.dataset.cost = "ai";
      current.addEventListener("click", async () => {
        const count = Number(state.total) || 0;
        const filters = currentFilterPayload();
        if (!count) { toast("当前筛选没有题目。", "error"); return; }
        const confirmed = await confirmDialog({
          title: `给当前这批 ${count} 道题生成标签建议？`,
          text: `只处理当前筛选出来的 ${count} 道题，包括还没显示在屏幕上的；想补整个题库请用上面那条。`
            + `这一次会调用 AI ${count} 次（每道题一次），会消耗额度。生成的标签只是建议，不会直接贴到题目上。`
            + "开始之后再改筛选，也不会扩大这次的范围。",
          ok: "开始生成"
        });
        if (confirmed) queueJobs("tags", { scope: "current_filter", filters }, "tags-current", count);
      });
      tagActions.push(current);
    }
    if (state.features.ai_answer) {
      const answers = iconButton("button", "button button-quiet button-small library-tool-action",
        `补齐缺的参考答案 · ${answerCount} 题`, "plus");
      answers.dataset.disabledInSelectedView = String(!apiReady || answerCount === 0);
      answers.dataset.queueAction = "answer";
      answers.dataset.cost = "ai";
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
      answerActions.push(answers);
    }
    // 1.25.20：两个筛选入口（建议待核对、没打标签）和三个 AI 动作（补标签、补当前
    // 这批、补答案）合成一处。它们本来就是同一件事——「还有活没干」，只是原来分在
    // 筛选行和批量行两处、两种长相，没有一样写着「让 AI 提标签建议」，老师因此以为
    // 这个功能不存在。筛选行现在只剩「来源」和「更多筛选」。
    //
    // 折叠起来的东西要让人看得见里面有什么，所以名字上直接把三样都数出来。
    // 「要不要花钱」从名字里挪走 —— 那是警告，不是动作，挤在名字里只会让人认不出
    // 这是干什么的；挪到展开后的第一行和每颗按钮的悬停说明里。
    if (!quickActions.length && !tagActions.length && !answerActions.length) {
      box.replaceChildren(); box.hidden = true; return;
    }
    box.hidden = false;
    const menu = node("details", "library-todo-menu");
    menu.open = todoMenuOpen;
    const working = queuedTags + queuedAnswers;
    if (working) menu.dataset.working = "true";
    const summary = node("summary", "library-todo-summary");
    // 1.25.44：三个数字原来是「440 等你核对 · 804 没知识点 · 539 没答案」。440 本来
    // 就包含在 804 里，并排写出来像 1783 件事。现在只说两个数，把「其中多少有建议」
    // 放在括号里，一眼看出是同一批活。
    const bits = [];
    if (missingCount) bits.push(pendingCount
      ? `${missingCount} 题没知识点（${pendingCount} 题有 AI 建议）`
      : `${missingCount} 题没知识点`);
    // 1.25.26：没开「AI 参考答案」时，面板里根本没有处理答案的入口。折叠条上先把
    // 「1 题没答案」写出来，点开却找不到任何能处理它的地方 —— 等于把老师引到死路。
    // 有入口才报这个数。
    if (answerCount && answerActions.length) bits.push(`${answerCount} 题没答案`);
    const paidActions = tagActions.length + answerActions.length;
    // 「没入库答案」按全库算。原来取的是当前筛选下的数，再去减全库的数，
    // 两个口径混着减，负数就是这么来的（出现过「其中 -37 题已有 AI 初稿」）。
    const noAnswerTotal = Number(state.tagWork?.missing_answer_total) || 0;
    const aiDraftAnswers = Number(state.tagWork?.ai_draft_answers) || 0;
    summary.append(node("span", "library-todo-lead", working ? "AI 正在处理" : "还有活没干"));
    summary.append(node("span", "library-todo-bits", bits.join(" · ")));
    if (working) summary.append(node("span", "library-batch-live", `处理中 ${working} 道`));
    summary.title = working
      ? `AI 正在逐题处理，还剩 ${working} 道。做完会自动显示，不用一直点着看。`
      : `点开看：${bits.join("；")}。`
        // 只有一栏时别提「分了两栏」：屏幕上并没有第二栏。
        + (answerActions.length ? "按「标签」和「答案」分了两栏；" : "")
        + (pendingCount && actionableCount
          ? `AI 建议里 ${actionableCount} 题现在就能采纳${outdatedCount ? `，另 ${outdatedCount} 题对不上当前标签库、要重新生成` : ""}；`
          : "")
        + (noAnswerTotal && answerActions.length
          ? `题库里没有入库答案的一共 ${noAnswerTotal} 题（全题库的数，不受筛选影响），`
            + `其中 ${aiDraftAnswers} 题已经有 AI 初稿、可以先保存下来；`
            + `剩下 ${answerCount} 题连 AI 初稿都没有，才需要让 AI 补。`
          : "")
        + (paidActions
          ? "没带「AI」标记的只筛一遍、不花钱，带「AI」的会让 AI 逐题处理、消耗额度。"
          : "这里只有筛选，点一下只是把题库筛一遍，不花钱，也不会调用任何服务。");
    menu.append(summary);

    const body = node("div", "library-todo-body");
    // 1.25.23：按「它动的是什么」分成两栏 —— 标签一栏、答案一栏。老师原话是这些
    // 按钮「无非分成两类，一个处理答案，一个处理标签」。平铺时确实看不出：
    // 「补齐缺的参考答案」和「补齐缺的知识点标签」长得一模一样，
    // 而「只处理当前这批 · 807 题」根本看不出它动的是标签。
    //
    // 每栏内部仍然是筛选在前、AI 在后 —— 先看不花钱的，再看要花钱的。
    // 筛选组挂自己的 class，不挂 library-tool-actions：applyBatchButtonState 按那个
    // class 圈按钮，挂了它，AI 一登记这两个免费的筛选就会跟着变灰（1.25.21 的毛病）。
    body.append(node("p", "library-todo-note", paidActions
      ? (apiReady
        ? "没带「AI」标记的只是筛一遍，不花钱；带「AI」的会让 AI 逐题处理，每题消耗一次额度。"
          + "标签只是建议，要你逐条核对确认后才贴到题目上。"
        : "到「设置 → API 配置」里配置并测试通过，才能用带「AI」标记的那几项。")
      // 面板里一颗花钱的按钮都没有时，不必讲额度：这里点的每一下都只是筛选。
      : "这里只有筛选，点一下只是把题库筛一遍，不花钱，也不会调用任何服务。"));
    // 排队中、以及「还在处理多少道」都写在这一行，正对着下面两栏。
    // 用户盯着按钮时一眼就能看见；只靠一闪而过的提示条必然会被错过。
    const stateLine = node("p", "library-batch-state");
    // 排队中这一行也要占住，而且必须能扛住重画：登记途中界面会重画，
    // 这一行每次都是新节点，所以排队文案要能按需重填，不能抱着旧节点不放。
    stateLine.dataset.tone = "working";
    stateLine.hidden = queueBusy ? false : !working;
    stateLine.textContent = queueBusy ? queueingText(queueCount, 0) : workingSummary(queuedTags, queuedAnswers);
    if (queueBusy) stateLine.dataset.queueing = "true"; else delete stateLine.dataset.queueing;
    body.append(stateLine);

    const cols = node("div", "library-todo-cols");
    // 只有标签这一栏有东西时（比如没开答案功能），别留一整列空白。
    if (!answerActions.length) cols.classList.add("library-todo-cols-solo");
    const column = (title, groups) => {
      const col = node("div", "library-todo-col");
      col.append(node("h4", "library-todo-col-title", title));
      const items = node("div", "library-todo-col-items");
      // 1.25.26：某一类没有东西时，上面传下来的是 null。这里以前直接读 group.children，
      // 于是「知识点标签」功能关着、而题库里又有没打标签的题时（默认就是这个状态），
      // 整个题库页白屏，只剩一行英文报错。没有东西可放就跳过，不要拿 null 当节点用。
      for (const group of groups) if (group && group.children.length) items.append(group);
      col.append(items);
      return col;
    };
    const freeGroup = () => {
      const g = node("div", "library-todo-look");
      g.append(...quickActions);
      return g;
    };
    const paidGroup = (actions) => {
      const g = node("div", "library-tool-actions");
      g.append(...actions);
      return g;
    };
    cols.append(column("标签", [
      quickActions.length ? freeGroup() : null,
      tagActions.length ? paidGroup(tagActions) : null,
    ].filter(Boolean)));
    if (answerActions.length) {
      cols.append(column("答案", [paidGroup(answerActions)]));
    }
    body.append(cols);
    menu.append(body);
    menu.addEventListener("toggle", () => { todoMenuOpen = menu.open; });
    box.append(menu);
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
      const stateLine = document.querySelector("#libraryTodos .library-batch-state");
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
      const stateLine = document.querySelector("#libraryTodos .library-batch-state");
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
  // 同上，看的是题库里有没有（state.presence），不是当前筛选下还剩几道。
  function renderReviewFilters(reviews) {
    const box = $("reviewFilters");
    const ai = Number(reviews.ai) || 0;
    const human = Number(reviews.human) || 0;
    box.hidden = !Number(state.presence?.review_ai) && !state.review;
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

  // 1.25.19：同一张卷连着几十道题，试卷名原来每张卡都写一遍，一屏能出现 17 次。
  // 现在只在这一串里的第一张写。判断必须放在「已经知道前后顺序」的地方做，不能
  // 埋在建卡函数里：题卡是复用的（签名没变就拿旧节点），上面那张被筛掉之后，
  // 这张会从「不写」变成「要写」，而它自己并不会重新建。展开题面还会整张换掉，
  // 所以渲染循环和展开那两处都要过一遍这里。
  function syncSourceLabel(node, item, prevItem) {
    const label = node.querySelector(".library-source");
    if (label) label.hidden = Boolean(prevItem) && prevItem.source_filename === item.source_filename;
  }

  // 1.25.26：选择题的题干常常只有一句「下列正确的是」，真正让人认出这道题的是选项。
  // 折叠的卡片上直接显示短选项，扫一眼就知道是哪道题；选项很长、或选项本身带图时仍然
  // 交给「展开题面」——不然一张卡能占掉半屏，挑题反而更慢。
  const OPTION_PREVIEW_LIMIT = 28;
  function previewOptions(content) {
    const options = content?.options || {};
    const keys = ["A", "B", "C", "D", "E"].filter((key) => String(options[key] ?? "").trim());
    if (!keys.length) return {};
    if ((content?.figures || []).some((figure) => ["A", "B", "C", "D", "E"].includes(figure.slot))) return {};
    // 去掉 $ \ { } 再量长短：公式记号不占纸面宽度。
    const widest = Math.max(...keys.map((key) => String(options[key]).replace(/[$\\{}]/g, "").length));
    return widest <= OPTION_PREVIEW_LIMIT ? options : {};
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
    const shownOptions = expanded ? {} : previewOptions(item.content);
    if (expanded) QB.renderQuestion(paper, item.content, { showNumber: false, showAnswer: "none" });
    else QB.renderQuestion(paper, { ...item.content, stem: summary.text, options: shownOptions, figures: (item.content?.figures || []).filter((figure) => figure.slot === "stem").slice(0, 2),
      question_images: (item.content?.question_images || []).slice(0, 1) }, { showNumber: false, showAnswer: "none" });
    article.classList.toggle("compact", !expanded);
    article.classList.toggle("is-selected", state.selected.has(item.id));
    if (!expanded) {
      const notes = [];
      if (item.content?.body_mode === "source_image") notes.push(`原卷截图 ${(item.content.question_images || []).length} 段`);
      if (summary.folded) notes.push("还有题干与小问");
      // 选项已经在卡片上看得见时就不再提它；长选项、选项带图仍然折叠。
      if (Object.keys(item.content?.options || {}).length && !Object.keys(shownOptions).length) notes.push("含选项");
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
      const previousCard = article.previousElementSibling;
      syncSourceLabel(replacement, item,
        previousCard ? state.catalog.get(previousCard.id.slice(2)) : null);
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
    else markInspectorToggle(document.body.classList.contains("library-inspector-open"));
  }

  function captureLibraryViewport() {
    const scroller = document.scrollingElement || document.documentElement;
    // inView 现在在模块层（见 renderInspector 上面那条），这里不再重复定义。
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
  // 1.25.76：把"暂无答案解析"加一颗"为本题写答案"的按钮 —— 老师看完题要在
  // 右侧面板顺手补答案，先要点"查看出处"再点"编辑答案解析"，跳两层。
  // 这一颗按钮直接到答案编辑器，少点一次；同样适用于"暂无正文"的小问。
  function inspectorFactAction(facts, label, text, actionLabel, onclick) {
    const row = node("div", "question-inspector-fact has-action");
    const textWrap = node("div", "question-inspector-fact-text");
    textWrap.append(node("dt", "", label), node("dd", "", text));
    const button = node("button", "button button-small question-inspector-fact-action", actionLabel);
    button.type = "button";
    button.addEventListener("click", onclick);
    row.append(textWrap, button);
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

  // 1.25.37：面板上的「定位题目」——把左边列表滚回这道题的位置。
  // 这题现在多半不在屏幕上时才有意义；在屏幕上时颗按钮自己藏起来，不占地方。
  //
  // 按钮是 HTML 里常驻的那一颗，不随题卡重建：第一版把它建在「标签」区标题栏上，
  // 而那一区只有题打过标签才渲染 —— 实机一验，没标签的题上这颗根本不出现。
  // 「面板顶部 + 与「收起」并排」才是每道题都在的位置。
  function syncLocateButton(item) {
    const button = $("locateQuestion");
    if (!button) return;
    button.hidden = !item || inView(document.getElementById(`q-${item.id}`));
  }

  function initLocateQuestion() {
    const button = $("locateQuestion");
    if (!button) return;
    button.addEventListener("click", () => {
      const id = state.inspectorId;
      const card = id ? document.getElementById(`q-${id}`) : null;
      if (!card) {
        // 题还在库里，只是没加载出来（比如还在后面几页）。说清去哪拿，别只弹一句「找不到」。
        toast("这道题还没加载出来，滚到底或点「加载更多」再找。", "error");
        return;
      }
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("locate-flash");
      window.setTimeout(() => card.classList.remove("locate-flash"), 1200);
    });
  }

  // 1.25.37：把 inView 从 selectInspectorQuestion 里提到模块层。
  // 它本来是个局部常量，syncLocateButton（在别的函数里）用不到它 —— 第一版
  // 直接照抄了那句调用，运行时抛 ReferenceError，按钮就一直是藏着的。
  const inView = element => {
    const rect = element?.getBoundingClientRect(); return rect && rect.bottom > 0 && rect.top < window.innerHeight;
  };

  function renderInspector(item) {
    if (!ui.inspectorContent) return;
    const renderKey = item ? JSON.stringify([item, state.features, inlineAdd.open && inlineAdd.itemId === item.id ? inlineAdd.query : null]) : "empty";
    if (renderKey === inspectorRenderedKey) return;
    inspectorRenderedKey = renderKey;
    const content = ui.inspectorContent;
    if (item && content.dataset.questionId === item.id) inspectorScrolls.set(item.id, content.scrollTop);
    content.dataset.questionId = item?.id || "";
    content.replaceChildren();
    syncLocateButton(item);
    if (!item) {
      const empty = node("div", "question-inspector-empty");
      empty.append(node("strong", "", "选择一道题"), node("p", "helper", "点击中间的题卡，这里可以给这道题整理标签。"));
      content.append(empty);
      if (ui.inspectorToggle) ui.inspectorToggle.disabled = true;
      // 禁用时也要说清为什么，否则只看到一个灰按钮，读者只能猜是自己哪里做错了。
      markInspectorToggle(document.body.classList.contains("library-inspector-open"), { disabled: true });
      return;
    }
    if (ui.inspectorToggle) ui.inspectorToggle.disabled = false;
    // 1.25.19：面板原来把左边题卡上的东西又抄了一遍 —— 题号、题型、试卷名，
    // 还有一个折叠的「查看当前题面」把整道题（含选项和公式）渲染了第二份；
    // 同一屏里试卷名一度出现 17 次。「当前选中 / 定位题卡」更是在说
    // 「这里讲的就是左边那道题」，而那道题本来就绿框标着，伸手就能点。
    // 面板只留左边没有、又真能做的事：整理标签。
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
    // 「来源」这一行原来和题卡、面板顶部的卷名是同一句话说了三遍。试卷名现在
    // 只在题卡上出现（同一张卷只写一次），这里就不再重复。
    inspectorFact(facts, "核对", item.review?.source === "ai" ? `${item.review.agent || "AI"} 审核 · 待人工核对`
      : item.review?.source === "human" ? "已人工核对" : "已入正式题库");
    // 1.25.76：右侧面板给老师一条最近的路 —— 缺答案的题从面板直接跳答案编辑器；AI
    // 参考答案未核对的题同样给一个"编辑 / 核对"入口，让"是否采纳 AI"在浏览器里走完。
    if (solutions.hasContent(item.solution)) {
      inspectorFact(facts, "答案解析", "已保存解析");
    } else if (item.has_answer) {
      inspectorFact(facts, "答案解析", "原卷提供答案");
    } else {
      const isAI = item.ai_answer && state.features.ai_answer;
      const text = isAI ? "有 AI 参考答案 · 未核对" : "暂无答案解析";
      inspectorFactAction(facts, "答案解析", text, isAI ? "编辑 / 核对答案解析" : "为本题写答案",
        () => openAnswerEditor([item], { scope: "library" }));
    }
    if (item.origin) inspectorFact(facts, "题源", item.origin);
    if (state.features.subquestions && item.subquestions >= 2) inspectorFact(facts, "小问", `${item.subquestions} 问`);
    info.append(facts);

    content.append(info);
    content.scrollTop = inspectorScrolls.get(item.id) || 0;
    markInspectorToggle(window.matchMedia("(max-width: 979px)").matches
      ? document.body.classList.contains("library-inspector-open") : !document.body.classList.contains("library-inspector-collapsed"));
  }

  // 这个按钮是个开关（开合一直跟着面板走），但字面只有「题目详情」四个字：不写现在
  // 是开着还是收着，也不说按了会发生什么。同一个页面上另一个收侧栏的开关就写着
  // 「收起筛选栏」—— 两个开关两套说法，读者只能猜哪一个是干什么的。
  // 状态和说法必须一起改，所以收在一处，别再散着写 aria-expanded。
  function markInspectorToggle(open, { disabled = false } = {}) {
    const toggle = ui.inspectorToggle;
    if (!toggle) return;
    const on = Boolean(open) && !disabled;
    toggle.setAttribute("aria-expanded", String(on));
    const label = disabled ? "先选中一道题" : (on ? "收起右侧详情" : "展开右侧详情");
    // 1.25.19：面板不再显示来源（试卷名只在题卡上出现一次），这里也跟着改。
    // 承诺了面板里没有的东西，比不说更糟 —— 老师会去找那道「来源」。
    toggle.title = disabled ? "先在左边点一道题，这里才有它的标签和待核对建议"
      : `${label}：当前题目的标签和待核对建议都写在这一栏`;
    toggle.setAttribute("aria-label", label);
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
    markInspectorToggle(next, { disabled: Boolean(ui.inspectorToggle?.disabled) });
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
  // 1.25.94：批量窗口刚打开时的落地页 —— 先摆「最近用过的 + 收藏」，不铺满两百多条。
  let tagPickerLanding = true;
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
    // 1.25.93：题卡上那颗标签原来一律绿、详情里那颗原来一律灰 —— 同一个标签两个样子。
    // 现在两处都用这颗标签自己的颜色（自己设的 → 同型祖先 → 按类型/学段取一个），
    // 跟教材目录、标签管理、标签选择窗口里那颗是同一个色。
    const chipColor = workspace.tagColor(tag, tagCatalogue.tags || []);
    if (chipColor) chip.style.setProperty("--tag-color", chipColor);
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
    // 1.25.94：提示语跟单题那边一样一句话 —— 两处是同一件事，别一个说「点击立即保存」、
    // 一个说「点一下贴上，再点一下拿掉」。
    const subtitle = node("p", "tag-picker-subtitle", "点一下贴上，再点一下拿掉。"); subtitle.id = "tagPickerSubtitle";
    title.append(heading, subtitle);
    const close = node("button", "button", "关闭"); close.type = "button"; close.addEventListener("click", function() { tagDialog.close(); });
    header.append(title, close);
    // 1.25.94：右上角那个孤零零的「新建标签」按钮去掉了 —— 新建入口统一放在列表最后一条，
    // 跟单题那边一模一样（「新建「XXX」并贴上」）。
    const search = node("input", "tag-picker-search"); search.id = "tagPickerSearch"; search.type = "search"; search.placeholder = "搜索名称、别名或说明";
    search.addEventListener("input", function() { tagPickerLanding = !search.value.trim(); renderTagPicker(); });
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
    more.append(moreTitle);
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
      ? "点一下贴上，再点一下拿掉。要采纳 AI 的建议，在右边详情面板上做。" : "点一下贴上，再点一下拿掉。";
    tagDialog.classList.add("loading");
    try {
      const ids = tagTargetIds.join(","), pair = await Promise.all([loadTagCatalogue(), tagRequest("/api/library/tags/assignments/batch?ids=" + encodeURIComponent(ids))]);
      tagRevisions = {}; tagMemberships = {};
      Object.entries(pair[1].items || {}).forEach(function(entry) {
        tagRevisions[entry[0]] = entry[1].revision;
        tagMemberships[entry[0]] = new Set(entry[1].assignments.filter(function(row) { return !row.needs_review; }).map(function(row) { return row.tag.id; }));
      });
      tagActiveSection = "all"; tagPickerView = "all"; tagUndoId = ""; $("tagPickerSearch").value = options.query || "";
      // 1.25.94：打开时先落在「最近用过的 + 收藏」，不铺满两百多条；要逛全部，
      // 点左边某一章，或点上面的「全部标签」。
      tagPickerLanding = !($("tagPickerSearch").value || "").trim();
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
      // 1.25.93：窗口里"已经在这道题上"的那几颗，跟题卡、详情、目录里是同一个颜色。
      const ownedColor = workspace.tagColor(tag, tagCatalogue.tags || []);
      if (ownedColor) chip.style.setProperty("--tag-color", ownedColor);
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
      // 1.25.94：落地页还没选任何一种看法时，别让「全部标签」亮着 —— 亮着就是骗人，
      // 列表上摆的是「最近用过的 + 收藏」。
      const active = tagPickerView === view.id && !tagPickerLanding;
      const button = node("button", "tag-group-button" + (active ? " active" : ""), view.name);
      button.type = "button";
      button.addEventListener("click", function() { tagPickerView = view.id; tagActiveSection = "all"; tagPickerLanding = false; renderTagPicker(); });
      quick.append(button);
    });
    const byId = new Map((tagCatalogue.tags || []).map(function(tag) { return [String(tag.id), tag]; }));
    const children = tagChildrenIndex();
    groupBox.replaceChildren();
    const allButton = node("button", "tag-group-button" + (tagActiveSection === "all" ? " active" : ""), "全部章节");
    allButton.type = "button"; allButton.addEventListener("click", function() { tagPickerView = "all"; tagActiveSection = "all"; tagPickerLanding = false; renderTagPicker(); }); groupBox.append(allButton);
    // 1.25.93：左栏原来把二十来个顶层标签平铺着排，知识点和解题方法混在一起，只能靠名字
    // 前缀去分是哪一类。现在按类型分组，跟标签管理页一致：先看是哪一类，再在类里挑。
    const rootTags = (tagCatalogue.tags || []).filter(function(tag) {
      return tag.active !== false && !(tag.parent_ids || []).some(function(pid) { return byId.has(String(pid)); });
    }).sort(function(left, right) {
      return left.name.localeCompare(right.name, "zh-CN");
    });
    ["knowledge", "method", "custom", "legacy"].forEach(function(kind) {
      const rows = rootTags.filter(function(tag) { return tag.kind === kind; });
      if (!rows.length) return;
      groupBox.append(node("span", "tag-picker-group-title", kindLabels[kind] || "标签"));
      rows.forEach(function(tag) {
        const button = node("button", "tag-group-button" + (tagActiveSection === String(tag.id) ? " active" : ""), tag.name);
        button.title = (kindLabels[tag.kind] || "标签") + " · 连同下面 " + (tagSubtree(String(tag.id), children).size - 1) + " 个下级标签";
        button.type = "button"; button.addEventListener("click", function() { tagPickerView = "all"; tagActiveSection = String(tag.id); tagPickerLanding = false; renderTagPicker(); }); groupBox.append(button);
      });
    });
    groupBox.scrollTop = groupScrollTop; groupBox.scrollLeft = groupScrollLeft;
    const query = String($("tagPickerSearch").value || "").trim().normalize("NFKC").toLocaleLowerCase();
    const active = tagActiveSection === "all" ? null : tagSubtree(tagActiveSection, children);
    const matchesText = function(tag) {
      const trail = tagTrail(tag, byId).join(" ");
      const text = [tag.name, tag.description, tag.conditions, ...(tag.aliases || []), trail].join(" ").normalize("NFKC").toLocaleLowerCase();
      return !query || text.includes(query);
    };
    let rows;
    if (tagPickerView === "recent") {
      // 1.25.94：「最近使用」跟单题那边共用同一份，顺序也一样。
      rows = recentTagsForAdd().filter(matchesText);
    } else {
      rows = (tagCatalogue.tags || []).filter(function(tag) {
        if (!tag.active) return false;
        if (tagPickerView === "favorite" && !tag.favorite) return false;
        if (!query && tagPickerView === "all" && active && !active.has(String(tag.id))) return false;
        return matchesText(tag);
      });
    }
    // 1.25.94：落地页 —— 先摆「最近用过的」和「收藏」，不铺满两百多条。
    // 以前一打开就是两百六十三条密密麻麻地摊在这儿，跟单题那边「打字才出候选」
    // 是两套完全不同的习惯；现在两边先给同一份「最近用过的」。
    const landing = tagPickerLanding && !query && tagActiveSection === "all" && tagPickerView === "all";
    const landingGroups = [];
    if (landing) {
      const recent = recentTagsForAdd();
      const favorites = (tagCatalogue.tags || []).filter(function(tag) { return tag.active !== false && tag.favorite; });
      if (recent.length) landingGroups.push({ title: "最近用过的", tags: recent });
      if (favorites.length) landingGroups.push({ title: "收藏", tags: favorites });
    }
    const scrollTop = list.scrollTop;
    list.replaceChildren();
    const renderRow = function(tag) {
      const count = tagTargetIds.reduce(function(total, id) { return total + (tagMemberships[id] && tagMemberships[id].has(tag.id) ? 1 : 0); }, 0);
      const single = tagTargetIds.length === 1;
      // 1.25.93：这三句原来说的是「这个标签」怎么样 —— 老师读起来会以为库里没这个标签。
      // 改成说「这几道题」怎么样，指的就是这次要整理的那几道题。
      const total = tagTargetIds.length;
      const text = single
        ? (count ? "✓ 已贴上" : "贴上")
        : count === total ? total + " 道都有" : count ? total + " 道里有 " + count + " 道" : total + " 道里都没有";
      const button = node("button", "tag-picker-row" + (count === tagTargetIds.length ? " active" : count ? " mixed" : ""));
      // 1.25.93：颜色统一算（自己设的 → 同型祖先 → 按类型/学段），跟教材目录、标签管理、
      // 题目详情、题卡里那颗是同一个色 —— 以前这里只看标签自己有没有设色，而老师的标签
      // 几乎都没设过，于是这一栏整片是灰的。
      const rowColor = workspace.tagColor(tag, tagCatalogue.tags || []);
      if (rowColor) button.style.setProperty("--tag-color", rowColor);
      button.type = "button"; button.disabled = tagDialog.classList.contains("saving");
      const title = node("span", "tag-picker-row-title");
      // 名字前加一颗彩色圆点：跟标签管理列表、教材目录里的标签长得一样。
      const nameLine = node("span", "tag-picker-row-name");
      const dot = node("span", "tag-picker-dot");
      if (rowColor) dot.style.setProperty("--tag-color", rowColor);
      nameLine.append(dot, node("strong", "", tag.name));
      // 1.25.94：一行只写「圆点 + 名字 + 上级路径」，跟单题那边同款；
      // 原来那个「知识点 · 初中 › 方程与不等式」的类型前缀和那段说明都挪到悬停里。
      const pathText = tagPathText(tag);
      title.append(nameLine, node("small", "tag-picker-row-path", pathText || (kindLabels[tag.kind] || "标签")));
      button.append(title, node("span", "tag-picker-row-state", text));
      button.title = tagRowTitle(tag);
      button.addEventListener("click", function() { void toggleTag(tag, count === tagTargetIds.length); });
      if (!single && count > 0 && count < tagTargetIds.length) {
        const mixed = node("div", "tag-picker-mixed-row"), remove = node("button", "button button-small", "全部移除"); remove.type = "button";
        button.querySelector(".tag-picker-row-state").textContent = text + " · 全部添加";
        remove.disabled = tagDialog.classList.contains("saving"); remove.addEventListener("click", () => void toggleTag(tag, true)); mixed.append(button, remove);
        return mixed;
      }
      return button;
    };
    if (landing) {
      if (!landingGroups.length) {
        list.append(node("p", "library-empty", "这里空着：先在上面搜一个标签名，或者从左边挑一章。"));
      } else {
        landingGroups.forEach(function(group) {
          list.append(node("span", "tag-picker-group-title", group.title));
          group.tags.forEach(function(tag) { list.append(renderRow(tag)); });
        });
      }
    } else if (!rows.length) {
      list.append(node("p", "library-empty", query ? "没有找到这个标签。" : "还没有可用标签。"));
    } else {
      rows.forEach(function(tag) { list.append(renderRow(tag)); });
    }
    // 1.25.94：新建入口统一放在列表最后一条（「新建「XXX」并贴上」），
    // 跟单题那边一样；右上角那个孤零零的「新建标签」按钮已经去掉。
    if (query) {
      const trimmed = $("tagPickerSearch").value.trim();
      const exact = (tagCatalogue.tags || []).find(function(tag) { return tag.active !== false
        && String(tag.name).toLowerCase() === trimmed.toLowerCase(); });
      if (!exact) {
        const create = node("button", "tag-picker-row is-create");
        create.type = "button";
        // 1.25.94：跟单题那边同款 —— 一行就是「名字」，圆点留给真标签。
        const createNameLine = node("span", "tag-picker-row-name");
        createNameLine.append(node("span", "library-inline-add-name", `新建「${trimmed.slice(0, 30)}」并贴上`));
        const createTitle = node("span", "tag-picker-row-title");
        createTitle.append(createNameLine);
        create.append(createTitle, node("span", "tag-picker-row-state", "建好就贴上"));
        create.title = "目录里没有这个标签；建好就贴到这次整理的这几道题上";
        create.addEventListener("click", function() { openTagCatalogueEditor(null, true); });
        list.append(create);
      }
    }
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
    // 1.25.44：知识点里出现过「不会」这种个人标记 —— 它会混进知识点筛选和 AI 建议，
    // 却不是知识点。新建时提醒一句，并给一键改成「自定义标签」。
    const personalMark = /^(不会|不会做|易错|易错题|重点|难点|粗心|待复习|错题|常考|必考)$/;
    const personalHint = node("small", "tag-catalogue-personal-hint"); personalHint.hidden = true;
    name.parentElement.append(personalHint);
    const syncPersonalHint = () => {
      const looksPersonal = !editing && kind.value === "knowledge" && personalMark.test(name.value.trim());
      personalHint.hidden = !looksPersonal;
      if (!looksPersonal) return;
      const switchKind = node("button", "button button-quiet button-small", "改成自定义标签");
      switchKind.type = "button";
      switchKind.addEventListener("click", () => { kind.value = "custom"; renderParents(); syncPersonalHint(); });
      personalHint.replaceChildren(
        node("span", "", `「${name.value.trim()}」更像个人标记，不是知识点；建成自定义标签就不会混进知识点筛选。`),
        switchKind);
    };
    name.addEventListener("input", syncPersonalHint); kind.addEventListener("change", syncPersonalHint);
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
    // 1.25.38：从「正在看的标签」里点新建时，上级已经替你选好了。写明建在谁下面，
    // 免得以为建的是一个顶级标签。
    if (!record && (defaults.parent_ids || []).length) {
      const names = (defaults.parent_ids || []).map(function(id) {
        return (tagCatalogue.tags || []).find(function(tag) { return tag.id === id; })?.name;
      }).filter(Boolean);
      if (names.length) {
        parentChoices.parentElement.append(node("small", "tag-catalogue-parent-hint",
          "会建在「" + names.join("、") + "」下面；不想挂在这里，就取消选中上面的上级。"));
      }
    }
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
      onClose: () => { void load({ quiet: true }); void refreshOpenTagPicker(); },
      // 1.25.12：标签管理页发现自己这 299 个标签几乎全是「0 题」时，会把人送回题库
      // 并直接筛出待核对的那一批 —— 少走一步「回来再找筛选」的路。
      onReviewSuggestions: () => {
        state.tag = ""; state.tagAll = ""; state.tagNot = ""; state.tagAny = "";
        state.tagMode = ""; state.tagReview = true; state.untaggedKnowledge = false;
        syncUrl(); void load();
      }
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
      // 1.25.78：开启"自动跳下一条"时，本题若已没有未处理建议，就跳到下一道仍有
      // pending 的题；让"全部采纳 + 全部拒绝"那种清空操作一遍过。
      if (state.autoAdvanceSuggestion && state.catalog.get(state.inspectorId)?.tag_suggestion?.status !== "pending") {
        const stillPending = visibleItems().filter(it => it.tag_suggestion && it.tag_suggestion.status === "pending"
          && (it.tag_suggestion.suggestions || []).some(e => !e.decision));
        const next = stillPending[0];
        if (next && next.id !== item.id) {
          const article = document.getElementById(`q-${next.id}`);
          if (article) { article.scrollIntoView({ block: "center" }); article.focus(); selectInspectorQuestion(next, article); }
        }
      }
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
    if (suggestion.status === "pending") {
      // 1.25.77：核对 440 条建议时反复回到主列表找下一张卡很费事。给面板头部加一颗
      // 上一条 / 下一条，按主列表的当前排序跳到下一道仍有 pending 建议的题；跳完左栏
      // 自动滚到目标，老师不用手找。键没有这种 pending 题时按钮置灰。
      const pendingItems = visibleItems().filter(it => it.tag_suggestion && it.tag_suggestion.status === "pending"
        && (it.tag_suggestion.suggestions || []).some(e => !e.decision));
      const currentIndex = pendingItems.findIndex(it => it.id === item.id);
      const goPrev = node("button", "button button-quiet button-small tag-suggestion-nav", "上一条");
      const goNext = node("button", "button button-quiet button-small tag-suggestion-nav", "下一条");
      goPrev.type = goNext.type = "button";
      const navigate = (delta) => {
        const target = pendingItems[(currentIndex + delta + pendingItems.length) % pendingItems.length];
        if (!target || target.id === item.id) return;
        const article = document.getElementById(`q-${target.id}`);
        if (article) {
          article.scrollIntoView({ block: "center" });
          article.focus();
          selectInspectorQuestion(target, article);
        } else {
          // 还没渲染（懒加载）就先翻页再回头重试
          toast("这一条还没载入，先滚到主列表的对应位置。");
        }
      };
      goPrev.disabled = pendingItems.length <= 1;
      goPrev.addEventListener("click", () => navigate(-1));
      goNext.disabled = pendingItems.length <= 1;
      goNext.addEventListener("click", () => navigate(1));
      const navRow = node("div", "tag-suggestion-nav-row");
      const counter = node("span", "tag-suggestion-nav-count",
        pendingItems.length > 1
          ? `第 ${currentIndex + 1} / ${pendingItems.length} 条`
          : "只有这一条");
      navRow.append(goPrev, counter, goNext);
      heading.append(navRow);
      // 嵌入式（面板被合并到主详情面板里）和非嵌入式（从弹窗打开）都要把导航放出来。
      if (embedded) panel.append(navRow);
    }
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
      // 1.25.75：AI 一次往往多条全部走查；勾三条点一次"采纳所选"比一条一条点快不了太多。
      // 直接给一颗"全部采纳"——一次性把这条建议上未处理的都采纳；后台会自动跳要选学段、要选上级的提醒，让老师补一遍再续做。
      const acceptAll = node("button", "button button-small button-primary tag-suggestion-accept-all", "全部采纳");
      acceptAll.type = "button";
      const allPendingIndices = (suggestion.suggestions || []).map((entry, i) => entry.decision ? -1 : i).filter(i => i >= 0);
      acceptAll.disabled = allPendingIndices.length === 0;
      acceptAll.addEventListener("click", () => resolveTagSuggestion(item, "accept", panel, allPendingIndices));
      const selectAll = node("button", "button button-quiet button-small tag-suggestion-select-all", "全选");
      const clearSelection = node("button", "button button-quiet button-small tag-suggestion-select-none", "全不选");
      selectAll.type = clearSelection.type = "button";
      selectAll.addEventListener("click", () => { checks.forEach(check => { check.checked = true; }); updateSelection(); });
      clearSelection.addEventListener("click", () => { checks.forEach(check => { check.checked = false; }); updateSelection(); });
      const accept = node("button", "button button-small", "采纳所选建议"); accept.type = "button"; accept.addEventListener("click", () => resolveTagSuggestion(item, "accept", panel));
      const reject = node("button", "button button-small", "拒绝所选建议"); reject.type = "button"; reject.addEventListener("click", () => resolveTagSuggestion(item, "reject", panel));
      const updateSelection = function() {
        const count = checks.filter(function(check) { return check.checked && !check.disabled; }).length;
        selection.textContent = count ? "已选 " + count + " 条建议" : "勾选要处理的建议";
        accept.disabled = reject.disabled = count === 0;
      };
      checks.forEach(function(check) { check.addEventListener("change", updateSelection); }); updateSelection();
      panel.addEventListener("suggestion-unlocked", updateSelection);
      buttons.append(acceptAll, accept, reject); actions.append(selection, buttons); panel.append(actions);
      const selectionToggles = node("div", "tag-suggestion-select-toggles");
      selectionToggles.append(selectAll, clearSelection); panel.append(selectionToggles);
    }
    if (busy) { panel.setAttribute("aria-busy", "true"); panel.querySelectorAll("button, input, select").forEach(control => { control.disabled = true; }); }
    return panel;
  }
  function cardTagContext(item) {
    const context = node("div", "library-card-context");
    const tagById = new Map((tagCatalogue.tags || []).map(t => [String(t.id), t]));
    const attached = (item.tag_assignments || []).filter(link => !link.needs_review && link.tag.kind === "knowledge");
    const isCovered = (link) => attached.some((other) => other !== link
      && ((tagById.get(String(other.tag.id)) || other.tag).parent_ids || []).some(pid => String(pid) === String(link.tag.id)));
    attached.filter(link => !isCovered(link)).slice(0, 2)
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
      if (body.presence) state.presence = body.presence;
      if (body.selected_tag_names) state.selectedTagNames = body.selected_tag_names;
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

  // 1.25.94：这道题上已经贴了哪些标签 —— 两处「加标签」都要用同一份。
  function takenTagIds(item) {
    return new Set((item.tag_assignments || []).filter(link => !link.needs_review).map(link => String(link.tag.id)));
  }

  // 1.25.94：「加标签」这两处（单题右栏、批量窗口）是同一件事，做成同一副样子：
  // 一条候选 = 彩色圆点 + 名字 + 上级路径。路径只写上级、用「›」连起来，
  // 不再带「知识点 · 」这种类型前缀（类型和说明都挪到鼠标放上去才出现）。
  function tagPathText(tag) {
    if (!tag) return "";
    const byId = new Map((tagCatalogue.tags || []).map(row => [String(row.id), row]));
    return tagTrail(tag, byId).join(" › ");
  }
  function tagRowTitle(tag) {
    if (!tag) return "";
    return [(kindLabels[tag.kind] || "标签"), tagPathText(tag), tag.description || tag.conditions]
      .filter(Boolean).join(" · ");
  }
  // 「最近用过的」：两处共用同一份，都排除停用的、历史标签和本题已经贴上的。
  function recentTagsForAdd(takenIds) {
    // takenIds 可能是数组，也可能是 Set（本题已贴的那些）—— 两种都收。
    const skip = new Set([...(takenIds || [])].map(String));
    const seen = new Set();
    return flowRecentTags().filter(function(tag) {
      if (!tag || tag.active === false || tag.kind === "legacy") return false;
      const id = String(tag.id);
      if (skip.has(id) || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }

  // 1.25.66：从当前题目上加一个标签，模式很直接 —— 点开「+ 添加标签」，边输边匹配，
  // 列表是目录里按名称/别名/路径打的分数；点一条就贴上，回车也行。
  // 没有开关弹窗、没有跳走。匹配不到去重，再到标签管理里建。
  const inlineAdd = { open: false, query: "", active: 0, itemId: null, busy: false };

  function inlineTagRank(tag, query) {
    const name = String(tag.name || "");
    const aliases = (tag.aliases || []).join(" ");
    const path = flowTagPath(tag);
    const lower = query.toLowerCase();
    const lowerName = name.toLowerCase();
    const lowerAliases = aliases.toLowerCase();
    const lowerPath = path.toLowerCase();
    let score = 0;
    if (lowerName === lower) score = 100;
    else if (lowerName.startsWith(lower)) score = 80;
    else if (lowerName.includes(lower)) score = 60;
    else if (lowerAliases.includes(lower)) score = 50;
    else if (lowerPath.includes(lower)) score = 30;
    if (!score) return 0;
    if (tag.kind === "knowledge") score += 6;
    if (tag.color) score += 2;
    return score;
  }

  function inlineAddMatches(item, query) {
    const q = String(query || "").trim();
    if (!q) return [];
    const taken = takenTagIds(item);
    const seen = new Set();
    const scored = [];
    for (const tag of tagCatalogue.tags || []) {
      if (tag.active === false) continue;
      const id = String(tag.id);
      if (taken.has(id)) continue;
      if (tag.kind === "legacy") continue;
      const score = inlineTagRank(tag, q);
      if (!score) continue;
      if (seen.has(id)) continue;
      seen.add(id);
      scored.push({ tag, score });
    }
    scored.sort((a, b) => b.score - a.score || String(a.tag.name).localeCompare(String(b.tag.name), "zh-CN"));
    return scored.slice(0, 8).map(entry => entry.tag);
  }

  async function applyInlineAdd(item, tag) {
    if (inlineTagLocks.has(item.id)) return;
    const expected = item.tag_revision;
    const attempt = { tagId: tag.id, tag, message: `已为本题贴上「${tag.name}」`, payload: {
      publication_ids: [item.id], add: [tag.id], remove: [], scopes: {},
      expected_revisions: { [item.id]: expected }, request_id: tagRequestId()
    } };
    inlineAdd.query = "";
    inlineAdd.active = 0;
    inlineAdd.open = true;             // 留着输入，下一条连贴
    await submitInlineTagAttempt(item, attempt);
    inspectorRenderedKey = "";
    renderInspector(item);
  }

  // 1.25.67：输入框里敲一个目录里没有的名字，回车就把它建出来并贴上，不再绕道标签管理。
  // 上级取当前筛的那一章（目录里点着哪章就建在哪章下）；没筛就按顶级建，学段跟着
  // 目录顶部选的学段走。建完立刻载入目录，拿到新标签后照常贴。
  async function createInlineTag(item, name) {
    if (inlineAdd.busy) return;
    const clean = String(name || "").trim().slice(0, 100);
    if (!clean) return;
    const parent = state.tagAny ? (tagCatalogue.tags || []).find(tag => String(tag.id) === String(state.tagAny)) : null;
    const kind = parent?.kind === "method" ? "method" : parent?.kind === "custom" ? "custom" : "knowledge";
    const levels = parent && (parent.levels || []).length ? parent.levels
      : directory.stage === "初中" || directory.stage === "高中" ? [directory.stage] : [];
    inlineAdd.busy = true;
    inlineAdd.query = "";
    inlineAdd.active = 0;
    renderInspector(item);
    try {
      const payload = { action: "create_tag", name: clean, kind, color: "", description: "", aliases: [],
        conditions: "", levels, parent_ids: parent ? [String(parent.id)] : [], related_method_ids: [],
        revision: tagCatalogue.revision, request_id: tagRequestId() };
      const result = await tagRequest("/api/library/tags", { method: "POST", body: JSON.stringify(payload) });
      tagCatalogue = result.catalogue || await loadTagCatalogue();
      const created = (tagCatalogue.tags || []).find(tag => tag.name === clean && tag.kind === kind);
      if (!created) throw new Error("标签已创建，但还没载入，请刷新后再贴。");
      toast(`已新建并贴上「${created.name}」`, "success");
      await applyInlineAdd(state.catalog.get(item.id) || item, created);
      if (document.body.classList.contains("library-directory-open")) directoryRender();
    } catch (error) {
      toast(error.message || "新建标签失败。", "error");
    } finally {
      inlineAdd.busy = false;
      inspectorRenderedKey = "";
      renderInspector(state.catalog.get(item.id) || item);
    }
  }

  function buildInlineAddPanel(item) {
    const panel = node("div", "library-inline-add");
    const field = node("label", "library-inline-add-field");
    const input = node("input", "library-inline-add-input");
    input.type = "text";
    input.placeholder = "输入标签名，回车贴上或新建";
    input.value = inlineAdd.query;
    input.autocomplete = "off";
    input.spellcheck = false;
    const close = node("button", "library-inline-add-close", "×");
    close.type = "button";
    close.title = "收起输入框";
    close.setAttribute("aria-label", "收起输入框");
    field.append(input, close);
    const list = node("div", "library-inline-add-list");
    list.setAttribute("role", "listbox");
    const hint = node("p", "library-inline-add-hint", "点一下贴上，再点一下拿掉。也可以 ↑↓ 选择、回车贴上。");

    // 一行一条候选；敲的名字在目录里找不到时，末尾补一条「新建并贴上」。
    const optionsFor = (query) => {
      const matches = inlineAddMatches(item, query);
      const rows = matches.map(tag => ({ type: "tag", tag }));
      const trimmed = query.trim();
      if (!trimmed) {
        // 1.25.94：空着的时候不给一片空白 —— 把「最近用过的」摆出来，点一下就贴上。
        // 这跟批量窗口打开时列的是同一份，两处不用各学一套。
        recentTagsForAdd(takenTagIds(item)).forEach(function(tag) {
          if (!rows.some(row => String(row.tag.id) === String(tag.id))) rows.push({ type: "tag", tag });
        });
        return rows;
      }
      // 「新建」只在目录里真的没有同名标签时出现；已经有了、只是本题没贴，那就不该再建一个。
      const exactTag = (tagCatalogue.tags || []).find(tag => tag.active !== false
        && String(tag.name).toLowerCase() === trimmed.toLowerCase());
      if (!exactTag) rows.push({ type: "create", name: trimmed });
      else if (!matches.some(tag => String(tag.id) === String(exactTag.id))) rows.push({ type: "note", text: `「${exactTag.name}」已经贴在这道题上了` });
      return rows;
    };

    const renderList = () => {
      const rows = optionsFor(inlineAdd.query);
      list.replaceChildren();
      if (!rows.length) {
        // 1.25.94：这里空着也得说清楚差哪一步、去哪做，不能光给一个空盒子。
        hint.hidden = false;
        hint.textContent = "这里空着：输入一个标签名就能找，或者到「标签管理」里建一个新的。";
        return;
      }
      // 1.25.94：这行话一直挂着 —— 老师得始终知道「点一下会贴上」。
      hint.hidden = false;
      hint.textContent = "点一下贴上，再点一下拿掉。也可以 ↑↓ 选择、回车贴上。";
      if (inlineAdd.active >= rows.length) inlineAdd.active = 0;
      rows.forEach((entry, index) => {
        const row = node("button", "library-inline-add-item" + (inlineAdd.active === index ? " active" : ""));
        row.type = "button";
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(inlineAdd.active === index));
        if (entry.type === "tag") {
          row.dataset.tagId = entry.tag.id;
          const color = workspace.tagColor(entry.tag, tagCatalogue.tags || []);
          if (color) row.style.setProperty("--tag-color", color);
          const dot = node("span", "library-inline-add-dot");
          if (!color) dot.hidden = true;
          const name = node("span", "library-inline-add-name", String(entry.tag.name));
          // 1.25.94：跟批量窗口同款 —— 有上级就写上级，没上级就写它是哪一类。
          const path = node("small", "library-inline-add-path", tagPathText(entry.tag) || (kindLabels[entry.tag.kind] || "标签"));
          row.append(dot, name, path);
          row.title = tagRowTitle(entry.tag);
          row.addEventListener("click", () => { void applyInlineAdd(item, entry.tag); });
        } else if (entry.type === "create") {
          row.classList.add("is-create");
          row.dataset.create = "1";
          const name = node("span", "library-inline-add-name", `新建「${entry.name.slice(0, 30)}」并贴上`);
          row.append(name);
          row.title = "目录里没有这个标签；回车会新建它，并建在当前选中章节下";
          row.addEventListener("click", () => { void createInlineTag(item, entry.name); });
        } else {
          row.classList.add("is-note");
          row.disabled = true;
          row.append(node("span", "library-inline-add-name", entry.text));
        }
        row.addEventListener("mouseenter", () => { inlineAdd.active = index; });
        list.append(row);
      });
    };

    input.addEventListener("input", () => { inlineAdd.query = input.value; inlineAdd.active = 0; renderList(); });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const rows = optionsFor(inlineAdd.query);
        if (!rows.length) return;
        inlineAdd.active = (inlineAdd.active + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
        renderList();
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const rows = optionsFor(inlineAdd.query);
        const entry = rows[inlineAdd.active] || rows[0];
        if (!entry) return;
        if (entry.type === "tag") void applyInlineAdd(item, entry.tag);
        else if (entry.type === "create") void createInlineTag(item, entry.name);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeInlineAdd(item);
      }
    });
    close.addEventListener("click", () => closeInlineAdd(item));
    panel.append(field, list, hint);
    renderList();
    return { panel, input };
  }

  function closeInlineAdd(item) {
    inlineAdd.open = false; inlineAdd.itemId = null; inlineAdd.query = ""; inlineAdd.active = 0;
    inspectorRenderedKey = "";
    if (item) renderInspector(item);
  }

  // 1.25.68：把一颗标签拖到另一颗标签上 → 它就成为子标签；父标签在本题上收起，
  // 让子标签顶它的位。底层走目录的 set_parents（操作记录里能撤销）。
  async function nestTagUnder(item, draggedTag, parentTag) {
    if (!draggedTag || !parentTag) return;
    if (String(draggedTag.id) === String(parentTag.id)) return;
    if (draggedTag.kind !== parentTag.kind) { toast("知识点和解题方法是两类，不能相互挂靠。", "error"); return; }
    // 不让拖成自己的后代（防环）。
    const descendantsOf = (root) => {
      const out = new Set();
      const stack = [(tagCatalogue.tags || []).find(t => String(t.id) === String(root.id))];
      while (stack.length) {
        const current = stack.pop();
        if (!current) continue;
        for (const child of (tagCatalogue.tags || [])) {
          if ((child.parent_ids || []).some(pid => String(pid) === String(current.id) && child.kind === current.kind)) {
            if (!out.has(String(child.id))) { out.add(String(child.id)); stack.push(child); }
          }
        }
      }
      return out;
    };
    if (descendantsOf(parentTag).has(String(draggedTag.id))) { toast("「" + draggedTag.name + "」是「" + parentTag.name + "」的子标签，把它挂回去会成环。", "error"); return; }
    try {
      const currentParents = (draggedTag.parent_ids || []).map(pid => String(pid));
      if (currentParents.includes(String(parentTag.id))) { toast("「" + draggedTag.name + "」已经挂到「" + parentTag.name + "」下面了。", "info"); return; }
      const action = "add_parent";
      const result = await tagRequest("/api/library/tags", { method: "POST", body: JSON.stringify({
        action, tag_id: String(draggedTag.id), parent_id: String(parentTag.id),
        revision: tagCatalogue.revision, request_id: tagRequestId()
      }) });
      tagCatalogue = result.catalogue || await loadTagCatalogue();
      toast("「" + draggedTag.name + "」已挂到「" + parentTag.name + "」下面", "success");
      inspectorRenderedKey = "";
      renderInspector(state.catalog.get(item.id) || item);
      if (document.body.classList.contains("library-directory-open")) directoryRender();
    } catch (error) {
      toast(error.message || "没挂上，原来的关系保留。", "error");
    }
  }

  function attachChipDnD(strip, item) {
    if (!strip || strip.dataset.dndAttached === "1") return;
    strip.dataset.dndAttached = "1";
    strip.querySelectorAll(".library-tag-chip[draggable='true']").forEach(chip => {
      chip.addEventListener("dragstart", (event) => {
        if (!chip.dataset.tagId) return;
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/x-tag", JSON.stringify({ id: chip.dataset.tagId, kind: chip.dataset.tagKind || "" }));
        chip.classList.add("is-dragging");
      });
      chip.addEventListener("dragend", () => {
        chip.classList.remove("is-dragging");
        strip.querySelectorAll(".is-drag-target").forEach(target => target.classList.remove("is-drag-target"));
      });
    });
    strip.addEventListener("dragover", (event) => {
      const chip = event.target.closest(".library-tag-chip[draggable='true']");
      if (!chip) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      if (!chip.classList.contains("is-drag-target")) chip.classList.add("is-drag-target");
    });
    strip.addEventListener("dragleave", (event) => {
      const chip = event.target.closest(".library-tag-chip[draggable='true']");
      if (chip) chip.classList.remove("is-drag-target");
    });
    strip.addEventListener("drop", async (event) => {
      const targetChip = event.target.closest(".library-tag-chip[draggable='true']");
      if (!targetChip) return;
      event.preventDefault();
      targetChip.classList.remove("is-drag-target");
      let payload; try { payload = JSON.parse(event.dataTransfer.getData("text/x-tag")); } catch (_) { return; }
      const draggedTag = (tagCatalogue.tags || []).find(t => String(t.id) === String(payload.id));
      const parentTag = (tagCatalogue.tags || []).find(t => String(t.id) === String(targetChip.dataset.tagId));
      if (!draggedTag || !parentTag) return;
      await nestTagUnder(item, draggedTag, parentTag);
    });
  }
  function extrasNode(item, { includeSuggestions = true } = {}) {
    const box = node("div", "library-extras");
    const row = node("div", "library-tags");
    // 1.25.68：父 + 子都贴在这道题上时，父就不画出来——让更具体的那颗顶着位。
    // 父标签是否被子标签盖住，要看最新的目录（拖完一颗标签，目录刚改过，本题缓存可能没动）。
    const tagById = new Map((tagCatalogue.tags || []).map(t => [String(t.id), t]));
    const attached = (item.tag_assignments || []).filter(link => !link.needs_review);
    const isCovered = (link) => attached.some((other) => {
      if (other === link) return false;
      const otherFresh = tagById.get(String(other.tag.id)) || other.tag;
      return (otherFresh.parent_ids || []).some(pid => String(pid) === String(link.tag.id) && otherFresh.kind === link.tag.kind);
    });
    const renderChips = attached.filter(link => !isCovered(link))
      .forEach((link) => {
      const tag = link.tag;
      const wrapper = node("div", "library-tag-chip"), chip = tagHoverChip(item, link, "library-tag");
      // 1.25.93：用统一算出来的颜色（题卡上那颗也是同一个），不再只看标签自己有没有设色。
      const wrapperColor = workspace.tagColor(tag, tagCatalogue.tags || []);
      if (wrapperColor) wrapper.style.setProperty("--tag-color", wrapperColor);
      wrapper.dataset.tagId = tag.id;
      wrapper.dataset.tagKind = tag.kind;
      wrapper.draggable = true;
      wrapper.title = `${tag.name} · 拖到另一颗标签上可把它挂成子标签`;
      const remove = node("button", "library-tags-remove", "×"); remove.type = "button"; remove.dataset.tagId = tag.id; remove.title = `从本题移除「${tag.name}」`; remove.setAttribute("aria-label", remove.title);
      remove.disabled = inlineTagLocks.has(item.id); remove.draggable = false; remove.addEventListener("click", () => removeInlineTag(item, tag)); wrapper.append(chip, remove);
      row.append(wrapper);
    });
    attachChipDnD(row, item);
    const edit = node("button", "library-tags-edit", "＋ 添加标签"); edit.type = "button";
    edit.title = "为当前这道题添加标签 —— 输入上文的标签名，按回车贴上；不打开弹窗"; edit.disabled = inlineTagLocks.has(item.id);
    edit.addEventListener("click", () => { inlineAdd.open = !inlineAdd.open; inlineAdd.itemId = item.id; inlineAdd.query = ""; inlineAdd.active = 0; renderInspector(item); }); row.append(edit); box.append(row);
    if (inlineAdd.open && inlineAdd.itemId === item.id) {
      const built = buildInlineAddPanel(item);
      box.append(built.panel);
      // 等下一贞再聚焦，保证面板挂在了 DOM 里。
      queueMicrotask(() => built.input.focus());
    }
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
    const label = kind === "tags" ? "生成标签建议" : "AI 生成答案";
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
      // 名字要说清是「生成建议」不是「贴标签」：老师看见「AI 建议标签」会以为这就是
      // 贴标签的按钮，贴不上就说功能坏了。这一颗管这一道题，批量条那颗管勾选的题。
      const button = iconButton("button", "button button-quiet button-small", busy ? "正在生成标签建议…" : prior ? "重新生成建议" : state.ai.mode === "assistant" ? "交给助手提标签建议" : "AI 生成标签建议", busy ? "" : "plus");
      button.disabled = busy;
      button.title = busy
        ? "正在生成标签建议…"
        : "让 AI 给这道题生成标签建议。生成的只是建议，要你逐条核对确认后才贴到题目上。每道题调用一次 AI，会消耗额度。";
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
    if (document.body.classList.contains("library-directory-open")) directoryRender();
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
    shown.forEach((item, index) => {
      const signature = JSON.stringify([item, state.basket.includes(item.id), state.features, state.ai.mode, state.selected.has(item.id), state.expanded.has(item.id), state.opened.has(item.id), state.tag, state.tagAll, state.tagAny, state.tagNot, state.tagMode]);
      const kept = previous.get(item.id);
      const node = kept && kept.signature === signature ? kept.node : card(item);
      syncSourceLabel(node, item, index > 0 ? shown[index - 1] : null);
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
      ["tag", state.tag ? `知识点：${state.tag}` : (state.tagAny && state.selectedTagNames?.[state.tagAny]) ? `标签：${state.selectedTagNames[state.tagAny]}` : (state.tagAll || state.tagAny || state.tagNot) ? "标签筛选已启用" : state.untaggedKnowledge ? "未标知识点" : state.tagReview ? "AI 建议待核对" : ""]];
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
      // 1.25.64：标签筛选条的颜色用同一标签的颜色 —— 目录看的颜色、筛选条看的颜色，
      // 详情里看到的颜色都是同一根色。改了标签颜色，这一栏会自动跟着变。
      if (key === "tag") {
        const tagId = state.tag || state.tagAny || state.tagAll || state.tagNot;
        if (tagId) {
          const t = tagCatalogue.tags.find(x => String(x.id) === String(tagId));
          // 1.25.93：筛选条上这颗标签也用统一算出来的颜色，跟目录、详情里那颗是同一个。
          const chipColor = t ? workspace.tagColor(t, tagCatalogue.tags || []) : "";
          if (chipColor) remove.style.setProperty("--tag-color", chipColor);
        }
      }
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
    // 1.25.78：在筛选行尾部加一颗"处理完自动跳下一条"开关。
    // 放在 activeFilters 而不是 libraryTodos 里，老师不用展开"还有活没干"的下拉就能看见。
    if (state.tagReview) {
      const adv = node("button", `library-auto-advance${state.autoAdvanceSuggestion ? " active" : ""}`,
        state.autoAdvanceSuggestion ? "✓ 处理完自动跳下一条" : "处理完自动跳下一条");
      adv.type = "button";
      adv.title = state.autoAdvanceSuggestion
        ? "已开启：提交当前题的建议后，自动跳到下一道仍有未处理建议的题。再点一下关掉。"
        : "点一下开启；开启后，处理完本题建议会自动滚到下一道有建议的题，方便清空一大批。";
      adv.setAttribute("aria-pressed", String(state.autoAdvanceSuggestion));
      adv.addEventListener("click", () => { state.autoAdvanceSuggestion = !state.autoAdvanceSuggestion; syncUrl(); renderActiveFilters(); });
      box.append(adv);
    }
  }

  // 「AI 生成标签建议」什么时候能用：功能开了、并且 API 配好测过了。
  // 单独抽出来是因为 syncSelection 里既要拿它决定灰不灰，又要拿它写悬停说明，
  // 两处各写一遍的话，改了一处忘了另一处就会说一套做一套。
  function suggestTagsReady() {
    return state.features.knowledge_tags === true && state.ai.mode === "api" && state.ai.api_ready === true;
  }
  // 灰着的按钮必须说清「为什么灰、接下来该怎么办」，而且三种原因要分开说：
  // 「功能没开」「还没填密钥」「填了但没测过」—— 原来一律写成「配置并测试通过」，
  // 老师换过密钥后没重测，看见这句话只会以为功能坏了。
  function suggestTagsTitle() {
    // 状态还没读到的时候不要下结论：页面刚打开那一刻这两样都是空的，
    // 说「功能没开」或「没配 API」都是冤枉人。
    if (state.ai.api_ready === undefined) return "正在读取 API 状态，请稍等一下。";
    if (state.features.knowledge_tags !== true) return "题库里没有开启「AI 生成标签建议」这项功能，要到「设置」里打开才能用。";
    const count = state.selected.size;
    if (state.ai.mode !== "api") return "现在是助手模式，不能自己调 API。到「设置 → API 配置」里切成独立 API 并测一次，这一项才能用。";
    if (state.ai.api_ready !== true) {
      if (state.ai.key_configured !== true) return "还没填 API 密钥。到「设置 → API 配置」里填上密钥、选好模型，再点一次「测试连接」，这一项才能用。";
      return "密钥填好了，但连接还没测过（换过密钥或改过模型都要重测）。到「设置 → API 配置」里点一次「测试连接」，这一项就能用。";
    }
    return count
      ? `让 AI 给勾选的 ${count} 道题生成标签建议。生成的只是建议，要你逐条核对确认后才贴到题目上。每道题调用一次 AI，会消耗额度。`
      : "先在左边勾几道题，再点这个让 AI 给它们生成标签建议。生成的只是建议，要你逐条核对确认后才贴到题目上。每道题调用一次 AI，会消耗额度。";
  }
  // 悬停看不见的东西等于没说：光靠 title 解释为什么灰，老师多半根本不会把鼠标
  // 放上去。所以按钮旁边常驻一行短提示，写清差哪一步、去哪做。
  function suggestTagsHint() {
    if (state.ai.api_ready === undefined) return "";
    if (state.features.knowledge_tags !== true) return "这项功能没开：到「设置」里打开";
    if (state.ai.mode !== "api") return "现在是助手模式：到「设置 → API 配置」切成独立 API";
    if (state.ai.api_ready !== true) return state.ai.key_configured === true
      ? "API 还没测过：到「设置 → API 配置」测一次"
      : "还没填 API 密钥：到「设置 → API 配置」填上";
    return "";
  }

  function syncSelection() {
    const shown = visibleItems();
    const count = shown.filter((item) => state.selected.has(item.id)).length;
    $("selectVisible").checked = Boolean(shown.length && count === shown.length);
    $("selectVisible").indeterminate = count > 0 && count < shown.length;
    $("selectVisible").disabled = !shown.length;
    // 1.25.31：加载的只是前几十道，还想全选当前筛选结果时，用这一颗。
    const selectAll = $("selectAllFiltered");
    if (selectAll) {
      const more = state.view !== "selected" && Number(state.total) > shown.length;
      selectAll.hidden = !more;
      if (more) selectAll.textContent = `选中全部 ${Number(state.total)} 道`;
    }
    $("selectionCount").textContent = `已勾选 ${state.selected.size} 题`;
    document.querySelector(".library-bulk")?.classList.toggle("has-selection", state.selected.size > 0);
    for (const [id, feature] of [["generateSelectedAnswers", "ai_answer"]]) {
      const button = $(id);
      button.hidden = !state.features[feature] || state.ai.mode !== "api" || state.ai.api_ready !== true;
      button.disabled = !state.selected.size;
    }
    // 1.25.20：「AI 生成标签建议」原来跟着另一颗一起藏，条件是「功能开了、API 配了、
    // 而且你已经勾了题」。老师什么都没勾的时候页面上一个入口都没有，于是以为这个
    // 功能不存在。改成常驻：任何时候都在，只是没勾题时灰着；灰着的时候要说清
    // 是要先勾题、还是要去开功能配 API —— 只看到一个灰按钮，等于没有入口。
    const suggest = $("generateSelectedTags");
    suggest.hidden = false;
    suggest.disabled = !state.selected.size || !suggestTagsReady();
    suggest.title = suggestTagsTitle();
    // 灰着的时候，旁边常驻一行话，写清差哪一步、去哪做。悬停说明看不见就等于没说。
    const hint = $("suggestTagsHint");
    if (hint) { hint.textContent = suggestTagsHint(); hint.hidden = !hint.textContent; }
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
      const locator = item ? sourceLocator(item) : `选题 ${index + 1}`;
      const name = item ? `${item.source_filename} · ${locator}` : locator;
      // 1.25.14：第二行原来印「文件名 · 第 4 题」，可题号在上一行的粗体里已经写过
      // 一遍。文件名一长就把「第 4 题」挤下去 —— 屏幕上留下「· 第」在上一行、「4 题」
      // 单独掉一行，三行的高度也跟着参差（57px / 40px / 40px）。现在这一行只说「这道题
      // 出自哪份卷子」，题号不再重复；完整那句留给悬停和读屏用的名字。
      const line = node("span", "basket-item-source", item ? item.source_filename : locator);
      line.title = name;
      open.append(node("strong", "", item ? `${locator} · ${QB.TYPE_NAMES[item.question_type] || "其他"}` : locator), line);
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
    // 1.25.13：篮子空的时候，这句话以前只叫人来把题放进来，却没说在哪儿放——面板里
    // 没有，题卡右下角那个按钮也没被提到过。走进空篮子的人除了关掉面板没有第二条路。
    // 现在把按钮的名字原样说出来，和题卡上写的是同一套话。
    $("basketSummary").textContent = state.basketLoading ? "正在核对选题的入库版本…"
      : !state.basket.length ? "篮子是空的。在左边任意一道题的右下角点「加入试题篮」，就会出现在这里；跨试卷、跨筛选都保留。"
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
  // 1.25.33：筛选行不再吸顶，批量条仍是唯一吸住的那一条，所以量它自己就够了。
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
      const stuck = bulkBar.getBoundingClientRect().top <= top + 1 && window.scrollY > 0;
      bulkBar.classList.toggle("stuck", stuck);
      // 1.25.31：滚过几屏以后才露「回到顶部」，免得刚打开就挡着题。
      const top_button = $("backToTop");
      if (top_button) top_button.hidden = window.scrollY < 900;
      // 当前那道题滚出屏幕时，「定位题目」才有意义（它在面板顶部，每道题都在）。
      syncLocateButton(state.inspectorId ? state.catalog.get(state.inspectorId) : null);
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
        // 1.25.82：右侧面板里"为本题写答案"是单题入口，老师刚答完一道要回到列表
        // 自己找下一道没答案的题 —— 不友好。存完之后自动在当前可见的列表里找下一道
        // 没有正式解析的题，直接选上并打开编辑器。多道（出卷草稿）那种入口不动。
        if (scope === "library" && answerEditor?.isOpen() && items.length <= 1) {
          const next = (state.items || []).find(candidate => candidate.id !== item.id
            && solutions.completeness(candidate) === "missing"
            && candidate.status !== "withdrawn");
          if (next) {
            state.inspectorId = next.id;
            const card = document.getElementById(`q-${next.id}`);
            if (card) { card.scrollIntoView({ block: "center" }); card.focus(); selectInspectorQuestion(next, card); }
            openAnswerEditor([next], { scope: "library" });
          } else {
            toast("当前筛选下没有更多没有答案解析的题了。", "success");
          }
        }
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
    // 1.25.15/1.25.16：这几颗按钮灰掉时各说各的为什么。刚打开时量到「分别导出题目卷与
    // 答案卷」的 title 是空的，而同一屏的「答案解析」一直带着说明；1.25.16 又量到把
    // 「输出内容」选成「答案与解析」、再关掉 AI 参考，「导出 Word」和「导出 PDF」一起灰掉、
    // 同样是空的 —— 偏偏这两颗是这一屏最常按的。
    // 四种原因各说各的：正在忙 / 选了答案卷可题没答案 / 有题没载入 / 图表排不进 A4。
    const busyWhy = printState.exporting ? "正在导出，请稍等"
      : printState.loading ? "正在载入题目和配图，请稍等" : "";
    const answerWhy = answerEmpty ? "输出内容选了「答案与解析」，可这次组卷的题没有答案或解析，先改选题目，或去「答案解析」里补上答案" : "";
    const missingWhy = !printState.items.length ? "先把未载入的选题处理掉，才能导出"
      : printState.missing.length ? `这次有 ${printState.missing.length} 道题没载入，先处理掉才能导出` : "";
    const layoutWhy = printState.layoutError ? `这份卷子有图表排不下：${printState.layoutError}`
      : printState.layoutPending ? "正在量这份卷子的版面，稍等一下"
      : printState.tooWide ? "有图表高于一张 A4，换个排法再导出" : "";
    const why = busyWhy || answerWhy || missingWhy;
    $("printButton").title = why || layoutWhy || "打开系统打印窗口，按预览里看到的分页印";
    $("exportWord").title = why || "下载可继续编辑的 Word 文档";
    $("exportPdf").title = why || layoutWhy || "下载按预览分页的 A4 PDF";
    const split = $("exportSplit");
    split.disabled = blocked || !printState.availableAnswers;
    split.title = !printState.items.length ? "先把未载入的选题处理掉，才能分别导出题目卷与答案卷"
      : printState.missing.length ? `这次有 ${printState.missing.length} 道题没载入，先处理掉才能分别导出`
      : !printState.availableAnswers ? "这次组卷的题还没有答案或解析，拆不出答案卷"
      : "导出两份文件：一份只有题目，一份只有答案与解析";
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
    // 1.25.15：上面那句话只说了「有没有答案」，没说因此按不了什么、怎么才能按。
    // 灰着的正好是两个入口：下面那个按钮，和「输出内容」里锁着的「答案与解析」。
    // 出路要点名写在这里 —— 页面说了出路却不说「所以这个按钮现在按不了」，人还是得自己拼。
    if (items.length && !available) {
      status.textContent += aiAvailable
        ? "「分别导出题目卷与答案卷」和「输出内容」里的「答案与解析」要等打开上面的「附 AI 参考（未核对）」才能用。"
        : "「分别导出题目卷与答案卷」和「输出内容」里的「答案与解析」都用不了，先在顶栏的「答案解析」里补上答案。";
    }
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

  // 1.25.16：没打开草稿时，「保存草稿」和「复制为新草稿」发的是同一个请求 —— copy 只影响
  // existing，而 existing 本来就是空的。同一个操作摆两个入口，第一次用的人只会愣一下。
  // 真打开了一份草稿之后，「复制为新草稿」才有意义（另存一份，原稿不动），那时候才出现。
  function syncDraftButtons() {
    const save = $("saveDraft"), copy = $("saveDraftAs"), title = state.draft?.title;
    copy.hidden = !state.draft;
    save.title = title ? `保存这次改动，覆盖正在编辑的「${title}」`
      : "把这次组卷存下来，下次从「草稿」里接着改";
    copy.title = `另存一份新草稿，正在编辑的「${title}」保持不动`;
  }

  function markDraftDirty() {
    syncDraftButtons();
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
        syncDraftButtons();
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
            syncDraftButtons();
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
    // 1.25.15：下面那句提示一次只讲得清一件事，它讲的是「这题另起一页」。可「本题留白」
    // 锁不锁是另一回事（非解答题、或答案紧跟题后），提示一个字没提，控件自己也没有悬停
    // 说明 —— 两件互不相干的事挤在一句话里，读的人猜不出哪句说的是哪个。
    $("printIndividualSpace").title = !item ? "先在上面选一道题，才能逐题调整"
      : item.question_type !== "free_response" ? "只对解答题有用，这道题留白没有意义"
      : $("printDocument").value === "answers" ? "输出内容选了「答案与解析」，卷上没有做题的空白"
      : ($("printDocument").value === "combined" && $("printAnswerLayout").value === "inline") ? "答案紧跟在题目后面，不留做题的空白"
      : "这一题在卷面上留多少空白给学生写过程";
    $("printIndividualBreak").title = !item ? "先在上面选一道题，才能逐题调整"
      : id === printState.firstQuestion ? "第一题本来就在第一页，不用再另起一页"
      : "让这道题从新的一页开始印";
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
  $("selectVisible").addEventListener("change", () => {    visibleItems().forEach((item) => { if ($("selectVisible").checked) state.selected.add(item.id); else state.selected.delete(item.id); });
    render();
  });
  // 1.25.31：「选中全部 N 道」——按当前筛选去后端只取编号，一次勾上（最多 500 道，
  // 和试题篮的上限一致；不弹任何确认，勾选本身不改任何东西，取消是「取消勾选」）。
  $("selectAllFiltered").addEventListener("click", async () => {
    const button = $("selectAllFiltered");
    const query = libraryQuery(state, { limit: 1, offset: 0 });
    query.delete("limit"); query.delete("offset");
    query.set("ids_only", "1");
    button.disabled = true;
    try {
      const response = await fetch(`/api/library?${query}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "读取题库失败");
      const ids = body.ids || [];
      ids.slice(0, 500).forEach((id) => state.selected.add(id));
      if (ids.length > 500) toast(`一次最多勾选 500 道，已勾上前 500 道。`, "");
      render();
    } catch (error) {
      toast(problemText(error), "error");
    } finally { button.disabled = false; }
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
  // 1.25.29：快捷键不再在抽屉里单独摆一颗按钮 —— 设置 → 帮助里有，题库页按「?」
  // 也能打开。同一个东西三个入口，抽屉里那颗反而像是「另一个页面」。
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
  // 1.25.31：滚到底自动接着加载。800 道题原来要点 19 次「加载更多」，每次 0.3 秒，
  // 一路点下来像卡住。提前一屏就开始取，按钮留着兜底（老浏览器没有观察器时照旧）。
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !ui.more.hidden && !state.loading) {
        load({ append: true });
      }
    }, { rootMargin: "600px 0px" }).observe(ui.more);
  }
  $("backToTop").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
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
    const ids = selectedTagIds().join(",");
    state.tag = ""; state.tagAll = state.tagAny = state.tagNot = "";
    if (state.tagMode === "not") state.tagNot = ids;
    else if (state.tagMode === "any") state.tagAny = ids;
    else state.tagAll = ids;
    syncUrl(); load();
  });
  $("tagMatchMode")?.addEventListener("change", () => {
    const selected = selectedTagIds().join(",");
    state.tagMode = $("tagMatchMode").value; state.tag = ""; state.tagAll = state.tagAny = state.tagNot = "";
    if (state.tagMode === "not") state.tagNot = selected; else if (state.tagMode === "any") state.tagAny = selected; else state.tagAll = selected;
    syncUrl(); load();
  });
  $("saveTagFilter")?.addEventListener("click", () => {
    const filter = currentTagFilter();
    if (!filter.legacy && !filter.all && !filter.any && !filter.not) {
      toast("先在「标签」里勾一个标签，再存。", "error");
      return;
    }
    // 起的名字只在本机用，说明把它能记住的范围写清楚：只存标签和关系。
    const name = window.prompt("给这组标签起个名字（例如「期中高频」）：", "");
    if (!name || !name.trim()) return;
    const saved = savedTagFilters().filter((item) => item.name !== name.trim()); saved.push({ name: name.trim(), filter: filter });
    try { localStorage.setItem("qb-library-saved-tag-filters", JSON.stringify(saved.slice(-30))); renderSavedTagFilters(); toast(`已记住「${name.trim()}」，以后在「已保存的」里选它。`, "success"); }
    catch (_) { toast("没能记住。关掉题有据重新打开再试一次。", "error"); }
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
  initLocateQuestion();
  $("inspectorScrim").addEventListener("click", () => setInspectorOpen(false));
  window.matchMedia("(max-width: 979px)").addEventListener("change", () => {
    document.body.classList.remove("library-inspector-open"); $("inspectorScrim").hidden = true;
    markInspectorToggle(!window.matchMedia("(max-width: 979px)").matches && !document.body.classList.contains("library-inspector-collapsed"));
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
  // 1.25.69：Ctrl+Z / Ctrl+Shift+Z 撤销/重做当前题上一次的标签改动。文字输入框
  // 里仍然是浏览器原生的"撤销改字"，由 target 检查交给浏览器，不抢它。
  document.addEventListener("keydown", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    if (event.key !== "z" && event.key !== "Z") return;
    if (event.altKey) return;
    if (document.querySelector("dialog[open]")) return;
    const editable = 'input:not([type=checkbox]):not([type=radio]), textarea, [contenteditable]:not([contenteditable="false"]), [role="textbox"]';
    if (event.target?.closest?.(editable)) return;
    event.preventDefault();
    const item = state.catalog.get(state.inspectorId);
    if (!item) return;
    if (inlineTagLocks.has(item.id)) { toast("上一步操作还没落地，稍等再撤销。", "info"); return; }
    if (event.shiftKey) {
      // 重做：当前只有 item 自带的"上一次操作"能撤销，目录里其它操作不带 operationId 到页面，
      // 这里就不冒进一条接出去的命令。重做以后再操作会盖住。
      toast("回题库用「＋ 添加标签」旁边的撤销按钮，或在标签管理里看操作记录。", "info");
      return;
    }
    const saved = inlineTagStates.get(item.id);
    if (!saved?.operationId) { toast("当前题目最近没有可撤销的标签操作。", "info"); return; }
    void undoInlineTag(item);
  });
  // 1.25.29：快捷键不再在抽屉里单独摆一颗按钮 —— 设置 → 帮助里有，这一页按「?」
  // 也能打开。同一个东西三个入口，抽屉里那颗反而像是「另一个页面」。
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
    // 1.25.8：题库页按空格打开当前选中题的全屏看题。以前这一页只认「/」和「?」，
    // 空格什么也不做 —— 按下去像软件没反应。焦点在按钮或折叠上时，空格仍然是
    // 「按一下就执行」，不能让路；一道题也没选中时也不拦，那时空格照旧往下翻页。
    if (ordinary && !event.shiftKey && (event.key === " " || event.code === "Space")
        && ui.sheet.hidden && !document.querySelector("dialog[open]")) {
      const current = state.inspectorId ? state.catalog.get(state.inspectorId) : null;
      if (current && !event.target?.closest?.("button, a[href], summary, [role='button']")) {
        event.preventDefault();
        openQuestionViewer(current, document.getElementById(`q-${current.id}`)?.querySelector(".library-full-button"));
      }
    }
    handlePrintEscape(event);
  });

  // ---------------------------------------------------------------- 教材目录
  // 1.25.58：目录长在题库里。左栏就是教材目录（章 → 节 → 知识点），每个节点写
  // 这一层有几道题；点一章直接看这一章的题（含子标签）。标签管理退回去只做编辑。
  const directory = { panel: null, loading: false, tags: [], open: new Set(), showAll: new Set(), error: "", kind: "knowledge", stage: "all" };
  const DIRECTORY_KINDS = { knowledge: "知识点", method: "解题方法", custom: "自定义" };

  function directoryChildren(tag) {
    const id = String(tag.id);
    return directory.tags.filter(child => child.kind === tag.kind
      && (child.parent_ids || []).some(parent => String(parent) === id))
      .sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN"));
  }

  function directoryCount(tag) {
    // 1.25.63：数字跟着当前筛选走。题库接口返回的标签计数已经算过来源、题型这些条件
    // （只是不算标签本身），目录直接用它 —— 筛了来源再看目录，数字和右边列表对得上。
    // 这份计数是一张列表（只在有题的标签上出现），目录里的每个节点都去里面找自己的那道数；
    // 载入之前先退回到目录目录里记的全库数字。
    const facets = state.facets?.tags;
    if (Array.isArray(facets) && facets.length) {
      const hit = facets.find(item => String(item.id) === String(tag.id));
      return hit ? Number(hit.subtree_count) || 0 : 0;
    }
    return Number(tag.subtree_count) || Number(tag.count) || 0;
  }

  // 1.25.93：颜色只算一次 —— 规则搬到共享的 library-workspace.js 了（标签管理页在另一个
  // 文件里，也得用同一套）。这里保留两个名字给目录用，实质都转给 workspace.tagColor。
  // 1.25.64 定的规则没变：自己设的 → 同型祖先 → 按类型/学段从调色板取。
  // 变的是**谁在用**这条规则：1.25.93 之前只有教材目录真算了，标签管理、标签选择
  // 窗口、题目详情、题卡各自只看「这颗标签自己有没有设颜色」—— 而老师的标签几乎都
  // 没设过颜色（302 个里只有 17 个章设了），于是同一个标签在五个地方是五种颜色。
  function directoryStageOf(tag) { return workspace.tagStageOf(tag); }
  function directoryColor(tag) { return workspace.tagColor(tag, directory.tags); }

  function directorySelected() { return String(state.tagAny || ""); }

  function directoryEnsure() {
    if (directory.panel) return directory.panel;
    const workspace = document.querySelector(".library-workspace");
    const panel = node("aside", "library-directory");
    panel.id = "libraryDirectory";
    panel.setAttribute("aria-label", "教材目录");
    panel.hidden = true;
    workspace.prepend(panel);
    // 1.25.87：右边缘加一根细拖拽手柄 —— 老师把目录拽宽一点，长名字（"三角形的三边关系"）
    // 就能完整显示，不用反复折子标签。
    const handle = node("span", "library-directory-resize");
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", "拖动调整目录宽度");
    handle.tabIndex = 0;
    panel.append(handle);
    directory.panel = panel;
    directory.resizeHandle = handle;
    applyDirectoryWidth();
    attachDirectoryResize(handle);
    return panel;
  }
  // 1.25.71：开关的点击事件一开始就挂上，不等首次打开。原先写在 directoryEnsure 里，
  // 上一次关掉后 localStorage 是 "0"，不会自动打开，监听也就没绑上，再点没反应。
  // 这里绑在初始化末尾 —— 在「if (state.document)...」之后，测试片段会忽略它。

  async function directoryToggle(force) {
    const panel = directoryEnsure();
    const open = typeof force === "boolean" ? force : !document.body.classList.contains("library-directory-open");
    document.body.classList.toggle("library-directory-open", open);
    panel.hidden = !open;
    $("libraryDirectoryToggle")?.setAttribute("aria-expanded", String(open));
    try { localStorage.setItem("qb-library-directory", open ? "1" : "0"); } catch (_) { /* 记不住也没关系 */ }
    if (!open) return;
    if (!directory.tags.length) await directoryLoad();
    // 1.25.72 之前默认把"真有题目"的章都展开 —— 老师看到的是一长串"还有 X 个没用到的"刷下来。
    // 1.25.83：再退一步 —— 默认就只展开"当前筛的那一章"（包括它的祖先），目录里剩下的
    // 都折成 ▸。老师想看哪一章自己点；清空筛选、关掉目录、下次再进来时不会又被展开一大排。
    // 展开到当前筛的那一层，免得「明明筛着函数，目录里却找不到它」。
    if (state.tagAny) directoryOpenPath(String(state.tagAny));
    directoryRender();
  }

  async function directoryLoad() {
    directory.loading = true; directory.error = "";
    directoryRender();
    try {
      const body = await tagRequest("/api/library/tags");
      directory.tags = (body.tags || []).filter(tag => tag.active !== false);
    } catch (error) {
      directory.error = error.message || "目录暂时无法载入。";
    } finally {
      directory.loading = false;
    }
  }

  function directoryOpenPath(id) {
    const byId = new Map(directory.tags.map(tag => [String(tag.id), tag]));
    const walk = (tag, seen) => {
      if (!tag) return;
      directory.open.add(String(tag.id));
      (tag.parent_ids || []).forEach(parentId => { if (!seen.has(String(parentId))) walk(byId.get(String(parentId)), new Set([...seen, String(parentId)])); });
    };
    walk(byId.get(String(id)), new Set([String(id)]));
  }

  function directoryRow(tag, depth, ancestors) {
    const id = String(tag.id);
    if (ancestors.has(id)) return null;
    const children = directoryChildren(tag).filter(child => !ancestors.has(String(child.id)));
    const open = directory.open.has(id);
    const subtreeEmpty = directoryCount(tag) === 0;
    // 1.25.83：深度 0 的章根（没有同类型上级的标签）是一级节点，子标签都更 ——
    // 章根加粗、字号略大、子标签灰一些，让整列层级一眼可分。
    const isChapterRoot = depth === 0;
    const row = node("div", "library-directory-row-wrap"
      + (directorySelected() === id ? " is-current" : "")
      + (subtreeEmpty ? " is-empty" : "")
      + (isChapterRoot ? " is-chapter" : " is-leaf"));
    // 1.25.89：子行是**嵌在父行里**的（下面 row.append(item)），父行的缩进已经把子行推过
    // 一次了。所以每层只加 CSS 里那**一格**（.is-leaf 的 padding-left），不能写 depth * 18 ——
    // 那样每层的 18 都会和祖先叠加：18 → 54 → 108 → 180，第四层在 240px 面板里一点字宽都
    // 不剩（折行后一行一个字）。老师看到的「字被挡住」根子在这里。
    row.style.setProperty("--directory-color", directoryColor(tag));
    const line = node("div", "library-directory-line");
    const toggle = children.length
      ? node("button", "library-directory-caret" + (open ? " open" : ""), open ? "▾" : "▸")
      : node("span", "library-directory-caret is-empty");
    if (children.length) {
      toggle.type = "button";
      toggle.setAttribute("aria-label", (open ? "收起" : "展开") + tag.name);
      toggle.addEventListener("click", (event) => {
        event.stopPropagation();
        if (open) directory.open.delete(id); else directory.open.add(id);
        directoryRender();
      });
    }
    const name = node("button", "library-directory-name", tag.name);
    name.type = "button";
    // 1.25.85：名字在面板里被截掉时，鼠标停上来能看见完整标签名 + 描述。description 之前
    // 单独顶在前面，名字被截后没法直接看到——放到 title 里一起 hover 看。
    name.title = tag.description ? `${tag.name} — ${tag.description}` : tag.name;
    if (directorySelected() === id) name.classList.add("selected");
    name.addEventListener("click", () => {
      directory.open.add(id);
      directorySelect(tag);
    });
    const count = node("small", "library-directory-count" + (subtreeEmpty ? " is-zero" : ""), `${directoryCount(tag)}`);
    // 1.25.93：章根是左边那条色条；子标签改画一颗彩色圆点 —— 跟标签管理列表、标签选择
    // 窗口里的标签长得一样。1.25.86 当初把子标签的色条去掉，是怕多行堆叠时色条连成一
    // 片；圆点不会连成一片，所以能加回来。
    if (!isChapterRoot) {
      const dot = node("span", "library-directory-dot");
      dot.setAttribute("aria-hidden", "true");
      line.append(dot);
    }
    line.append(toggle, name, count);
    row.append(line);
    if (open) {
      const used = children.filter(child => directoryCount(child) > 0);
      // 1.25.72：子标签全部空的时候，"还有 N 个没用到的"也变成纯噪音 —— 留一行就够，
      // 明确告诉用户"这一支还空着，要加标签到标签管理"，别再加一个箭头。
      const allChildrenEmpty = children.length > 0 && used.length === 0;
      const hidden = children.filter(child => directoryCount(child) === 0);
      const showAll = directory.showAll.has(id);
      const list = showAll ? children : used;
      list.forEach(child => { const item = directoryRow(child, depth + 1, new Set([...ancestors, id])); if (item) row.append(item); });
      if (hidden.length && !showAll && !allChildrenEmpty) {
        const more = node("button", "library-directory-more", `还有 ${hidden.length} 个没用到的`);
        more.type = "button";
        more.addEventListener("click", () => { directory.showAll.add(id); directoryRender(); });
        more.style.marginLeft = "18px";
        row.append(more);
      }
    }
    return row;
  }

  // 1.25.87：拖拽调整目录宽度 —— 老师把目录拽宽一点，长名字能完整显示。
  // localStorage 里记着宽，下次进来保持；最小 200px，最大 480px。
  const DIRECTORY_WIDTH_KEY = "qb-library-directory-width";
  function readDirectoryWidth() {
    const raw = Number(localStorage.getItem(DIRECTORY_WIDTH_KEY));
    return Number.isFinite(raw) && raw >= 200 && raw <= 480 ? raw : 240;
  }
  function applyDirectoryWidth() {
    const width = readDirectoryWidth();
    document.documentElement.style.setProperty("--directory-width", width + "px");
  }
  function writeDirectoryWidth(width) {
    try { localStorage.setItem(DIRECTORY_WIDTH_KEY, String(width)); } catch (_) { /* 记不住就忘掉 */ }
  }
  function attachDirectoryResize(handle) {
    let dragging = false;
    let startX = 0;
    let startWidth = 0;
    const onMove = (event) => {
      if (!dragging) return;
      const dx = event.clientX - startX;
      // 拖向右边宽度变大；clamp 到 200–480 让长名字能展开，但不让它把主区挤垮。
      const next = Math.max(200, Math.min(480, Math.round(startWidth + dx)));
      document.documentElement.style.setProperty("--directory-width", next + "px");
    };
    const onUp = (event) => {
      if (!dragging) return;
      dragging = false;
      const dx = event.clientX - startX;
      const next = Math.max(200, Math.min(480, Math.round(startWidth + dx)));
      writeDirectoryWidth(next);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("is-resizing");
    };
    const onDown = (event) => {
      if (event.button && event.button !== 0) return;
      dragging = true;
      startX = event.clientX;
      startWidth = readDirectoryWidth();
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
      document.body.classList.add("is-resizing");
      event.preventDefault();
    };
    handle.addEventListener("pointerdown", onDown);
    // 键盘也能调：左右方向键各 8px，PgUp/PgDn 各 64px，Home/End 跳到边界。
    handle.addEventListener("keydown", (event) => {
      const step = event.key === "PageUp" || event.key === "PageDown" ? 64
        : event.key === "Home" ? 240 - readDirectoryWidth()
        : event.key === "End" ? 480 - readDirectoryWidth()
        : event.key === "ArrowLeft" || event.key === "ArrowRight" ? (event.shiftKey ? 24 : 8) : 0;
      if (!step) return;
      const dir = event.key === "ArrowLeft" || event.key === "PageUp" || event.key === "Home" ? -1 : 1;
      const next = Math.max(200, Math.min(480, readDirectoryWidth() + dir * step));
      document.documentElement.style.setProperty("--directory-width", next + "px");
      writeDirectoryWidth(next);
      event.preventDefault();
    });
  }

  function directoryRender() {
    const panel = directory.panel;
    if (!panel) return;
    const scroll = panel.scrollTop;
    panel.replaceChildren();
    const head = node("div", "library-directory-head");
    head.append(node("strong", "", "教材目录"));
    const close = node("button", "button button-small button-quiet", "收起"); close.type = "button";
    close.addEventListener("click", () => void directoryToggle(false));
    head.append(close);
    panel.append(head);
    panel.append(node("p", "library-directory-help", "点一章看这一章的题；0 表示当前筛选下这一章没有题（多半是还没贴标签）。"));
    if (directory.loading) { panel.append(node("p", "library-directory-empty", "正在读取目录…")); return; }
    if (directory.error) {
      const retry = node("button", "button button-small", "重新读取"); retry.type = "button";
      retry.addEventListener("click", () => void directoryLoad().then(directoryRender));
      panel.append(node("p", "library-directory-empty", directory.error), retry);
      return;
    }
    // 1.25.63：解题方法也是一层层的方法目录（代数与函数 → 因式分解 …），以前只能去标签管理里
    // 看。目录顶部给一个分类切换，两种文件夹都在题库里。
    const kindRows = Object.entries(DIRECTORY_KINDS)
      .map(([kind, label]) => ({ kind, label, total: directory.tags.filter(tag => tag.kind === kind).length }))
      .filter(entry => entry.total);
    if (kindRows.length > 1) {
      const kinds = node("div", "library-directory-kinds");
      kindRows.forEach(entry => {
        const button = node("button", "library-directory-kind" + (directory.kind === entry.kind ? " selected" : ""), `${entry.label} ${entry.total}`);
        button.type = "button";
        button.addEventListener("click", () => {
          if (directory.kind === entry.kind) return;
          directory.kind = entry.kind; directory.open.clear(); directory.showAll.clear();
          directoryRender();
        });
        kinds.append(button);
      });
      panel.append(kinds);
    }
    // 1.25.67：学段单独一行。网友提的「学段划分」落在目录上最值：初中、高中分开看，
    // 两边的章不混在一起，找题的路少一半。学科不单列 —— 这是个数学题库，没有第二科。
    const stageCounts = { 初中: 0, 高中: 0 };
    directory.tags.filter(tag => tag.kind === directory.kind).forEach(tag => {
      const stage = directoryStageOf(tag);
      if (stage in stageCounts) stageCounts[stage] += 1;
    });
    if (stageCounts.初中 || stageCounts.高中) {
      const stages = node("div", "library-directory-stages");
      [["all", "全部"], ["初中", "初中"], ["高中", "高中"]].forEach(([value, label]) => {
        if (value !== "all" && !stageCounts[value]) return;
        const button = node("button", "library-directory-stage" + (directory.stage === value ? " selected" : ""), label);
        button.type = "button";
        button.addEventListener("click", () => {
          if (directory.stage === value) return;
          directory.stage = value; directory.open.clear(); directory.showAll.clear();
          directoryRender();
        });
        stages.append(button);
      });
      panel.append(stages);
    }
    const all = node("button", "library-directory-all", "全部题目");
    all.type = "button";
    all.classList.toggle("selected", !state.tagAny && !state.untaggedKnowledge);
    all.addEventListener("click", () => directorySelect(null));
    panel.append(all);
    const missing = Number(state.tagWork?.untagged_knowledge) || 0;
    if (missing) {
      const untagged = node("button", "library-directory-all", `未标知识点 · ${missing} 题`);
      untagged.type = "button";
      untagged.title = "整个题库里还没打上正式知识点标签的题数，不受上面的来源、题型影响。";
      untagged.classList.toggle("selected", state.untaggedKnowledge);
      untagged.addEventListener("click", () => {
        state.untaggedKnowledge = true; state.tagAny = ""; state.tagAll = ""; state.tagNot = ""; state.tag = ""; state.tagReview = false;
        syncUrl(); void load();
        if (window.matchMedia("(max-width: 1099px)").matches) void directoryToggle(false);
      });
      panel.append(untagged);
    }
    const byId = new Map(directory.tags.map(tag => [String(tag.id), tag]));
    const roots = directory.tags.filter(tag => tag.kind === directory.kind
      && !(tag.parent_ids || []).some(parent => byId.get(String(parent))?.kind === directory.kind)
      && (directory.stage === "all" || directoryStageOf(tag) === directory.stage))
      .sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN"));
    if (!roots.length) panel.append(node("p", "library-directory-empty", directory.stage === "all"
      ? `还没有${DIRECTORY_KINDS[directory.kind] || "这一类"}标签。`
      : `${directory.stage}这边还没有${DIRECTORY_KINDS[directory.kind] || "这一类"}标签。`));
    roots.forEach(root => { const row = directoryRow(root, 0, new Set()); if (row) panel.append(row); });
    // 1.25.87：directoryRender 用了 replaceChildren 把面板内容清空重建，顺手把手柄再贴回去。
    if (directory.resizeHandle) panel.append(directory.resizeHandle);
    panel.scrollTop = scroll;
  }

  function directorySelect(tag) {
    if (tag) {
      state.tagAny = String(tag.id); state.tagMode = "any";
      state.tagAll = ""; state.tagNot = ""; state.tag = ""; state.tagReview = false; state.untaggedKnowledge = false;
    } else {
      state.tagAny = ""; state.tagAll = ""; state.tagNot = ""; state.tag = "";
      state.untaggedKnowledge = false; state.tagReview = false;
    }
    syncUrl(); void load();
    directoryRender();
    if (window.matchMedia("(max-width: 1099px)").matches) void directoryToggle(false);
  }

  // 1.25.54：把「给题库贴知识点」变成一条流水线。一次一道题，输入即搜、回车贴上
  // 并自动下一题、空格跳过。它解决的是「800 道题、300 个标签，一道道开对话框找树」
  // 这件事 —— 标签库先要有内容，目录再漂亮才有意义。
  const tagFlow = { dialog: null, items: [], index: 0, total: 0, offset: 0, hasMore: false,
    loading: false, busy: false, results: [], active: 0, undoId: "", done: false, error: "",
    recentIds: new Set(), returnFocus: null };
  const TAG_FLOW_RECENT = "qb-library-recent-tags";

  function flowTagById(id) { return (tagCatalogue.tags || []).find(tag => tag.id === id); }

  function flowRecentTags() {
    let ids = [];
    try { ids = JSON.parse(localStorage.getItem(TAG_FLOW_RECENT) || "[]"); } catch (_) { ids = []; }
    const seen = new Set();
    return ids.slice(-12).reverse().map(flowTagById).filter(tag => tag && !seen.has(tag.id) && seen.add(tag.id)).slice(0, 8);
  }

  function flowRecentIds() {
    try { return new Set(JSON.parse(localStorage.getItem(TAG_FLOW_RECENT) || "[]")); } catch (_) { return new Set(); }
  }

  function rememberFlowTag(id) {
    try {
      const ids = JSON.parse(localStorage.getItem(TAG_FLOW_RECENT) || "[]").filter(value => value !== id);
      ids.push(id);
      localStorage.setItem(TAG_FLOW_RECENT, JSON.stringify(ids.slice(-20)));
      tagFlow.recentIds = flowRecentIds();
    } catch (_) { /* 本地偏好存不下就不记 */ }
  }

  function flowTagPath(tag) {
    const path = tagAncestorPaths(tag.id, 2).paths[0] || [];
    // 路径包含标签自己，结果行里名字已经写了，这里只留上级。
    return path.slice(0, -1).map(value => value.name).join(" › ");
  }

  // 搜索范围是全部类型的有效标签；知识点排前面（这条流水的目标就是补知识点），
  // 方法和自定义标签也能贴，但排在后面。
  function flowMatches(query) {
    const q = String(query || "").trim().toLowerCase();
    if (!q) return [];
    const scored = [];
    for (const tag of tagCatalogue.tags || []) {
      if (tag.active === false) continue;
      const name = String(tag.name || "").toLowerCase();
      const aliases = (tag.aliases || []).join(" ").toLowerCase();
      const path = flowTagPath(tag).toLowerCase();
      let score = 0;
      if (name === q) score = 100;
      else if (name.startsWith(q)) score = 80;
      else if (name.includes(q)) score = 60;
      else if (aliases.includes(q)) score = 50;
      else if (path.includes(q)) score = 30;
      if (!score) continue;
      if (tag.kind === "knowledge") score += 6;
      if (tagFlow.recentIds.has(tag.id)) score += 4;   // 最近用过的，往上提一点
      scored.push({ tag, score });
    }
    scored.sort((a, b) => b.score - a.score || String(a.tag.name).localeCompare(String(b.tag.name), "zh-CN"));
    return scored.slice(0, 8).map(entry => entry.tag);
  }

  function flowCurrent() { return tagFlow.items[tagFlow.index] || null; }

  function flowStatus(text, error = false) {
    const status = tagFlow.dialog?.querySelector("#tagFlowStatus");
    if (!status) return;
    status.textContent = text || "";
    status.dataset.error = String(Boolean(error));
  }

  function flowBuild() {
    if (tagFlow.dialog) return tagFlow.dialog;
    const dialog = node("dialog", "tag-flow");
    dialog.id = "tagFlowDialog";
    dialog.setAttribute("aria-labelledby", "tagFlowTitle");
    const head = node("header", "tag-flow-head");
    const titles = node("div", "tag-flow-titles");
    const title = node("strong", "", "逐题贴标签"); title.id = "tagFlowTitle";
    const progress = node("small", "tag-flow-progress"); progress.id = "tagFlowProgress";
    titles.append(title, progress);
    const close = node("button", "button button-quiet", "退出"); close.type = "button";
    close.addEventListener("click", () => closeTagFlow());
    head.append(titles, close);
    const body = node("div", "tag-flow-body");
    const question = node("section", "tag-flow-question");
    const meta = node("div", "tag-flow-meta");
    const stem = node("div", "tag-flow-stem");
    const current = node("div", "tag-flow-current"); current.id = "tagFlowCurrent";
    question.append(meta, stem, current);
    const picker = node("section", "tag-flow-picker");
    const label = node("label", "tag-flow-search");
    const input = node("input"); input.type = "search"; input.id = "tagFlowSearch";
    input.placeholder = "输入标签名，例如 函数"; input.autocomplete = "off";
    input.setAttribute("aria-label", "搜索要贴的标签");
    label.append(node("span", "tag-flow-search-label", "贴一个标签"), input);
    const results = node("div", "tag-flow-results"); results.id = "tagFlowResults";
    const suggest = node("div", "tag-flow-suggest"); suggest.id = "tagFlowSuggest"; suggest.hidden = true;
    const recent = node("div", "tag-flow-recent"); recent.id = "tagFlowRecent";
    picker.append(label, suggest, results, recent);
    body.append(question, picker);
    const foot = node("footer", "tag-flow-foot");
    const hint = node("span", "tag-flow-hint", "上下键选择 · 回车贴上并下一题 · 空格跳过 · Esc 退出");
    const status = node("span", "tag-flow-status"); status.id = "tagFlowStatus";
    const undo = node("button", "button button-small tag-flow-undo", "撤销上一次"); undo.type = "button"; undo.id = "tagFlowUndo"; undo.hidden = true;
    undo.addEventListener("click", () => void undoFlowTag());
    foot.append(hint, status, undo);
    dialog.append(head, body, foot);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeTagFlow(); });
    dialog.addEventListener("keydown", (event) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!tagFlow.results.length) return;
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        tagFlow.active = (tagFlow.active + delta + tagFlow.results.length) % tagFlow.results.length;
        paintFlowResults();
      } else if (event.key === "Enter") {
        const tag = tagFlow.results[tagFlow.active] || tagFlow.results[0];
        if (!tag) return;
        event.preventDefault();
        void applyFlowTag(tag);
      } else if (event.key === " " || event.code === "Space") {
        // 空格只在搜索框空着时才是「跳过」；输入框里有字时，空格照常打。
        if (event.target === input && input.value.trim()) return;
        if (event.target?.closest?.("button")) return;
        event.preventDefault();
        skipFlowQuestion();
      }
    });
    input.addEventListener("input", () => {
      tagFlow.results = flowMatches(input.value);
      tagFlow.active = 0;
      renderFlowResults();
    });
    tagFlow.dialog = dialog;
    document.body.append(dialog);
    return dialog;
  }

  async function openTagFlow() {
    tagFlow.returnFocus = document.activeElement;
    if (!tagCatalogue.tags?.length) {
      try { await loadTagCatalogue(); }
      catch (error) { toast(error.message || "标签目录暂时无法载入。", "error"); return; }
    }
    const dialog = flowBuild();
    if (dialog.open) { dialog.querySelector("#tagFlowSearch")?.focus(); return; }
    tagFlow.items = []; tagFlow.index = 0; tagFlow.offset = 0; tagFlow.hasMore = false;
    tagFlow.done = false; tagFlow.error = ""; tagFlow.results = []; tagFlow.active = 0;
    tagFlow.undoId = ""; tagFlow.loading = true; tagFlow.recentIds = flowRecentIds();
    dialog.showModal();
    tagFlow.dialog.querySelector("#tagFlowSearch").value = "";
    renderTagFlow();
    try {
      const body = await fetch("/api/library?untagged_knowledge=true&limit=40&offset=0", { cache: "no-store" }).then(response => response.json());
      tagFlow.items = body.items || [];
      tagFlow.total = Number(body.total) || tagFlow.items.length;
      tagFlow.hasMore = Boolean(body.has_more);
      tagFlow.offset = tagFlow.items.length;
    } catch (error) {
      tagFlow.error = error.message || "读取题库失败。";
    }
    tagFlow.loading = false;
    renderTagFlow();
    tagFlow.dialog.querySelector("#tagFlowSearch").focus();
  }

  function closeTagFlow() {
    const dialog = tagFlow.dialog;
    if (dialog?.open) dialog.close();
    if (tagFlow.returnFocus?.isConnected) tagFlow.returnFocus.focus({ preventScroll: true });
    if (state.untaggedKnowledge) void load({ quiet: true });
  }

  async function flowLoadMore() {
    if (!tagFlow.hasMore || tagFlow.loading) return;
    tagFlow.loading = true;
    try {
      const body = await fetch(`/api/library?untagged_knowledge=true&limit=40&offset=${tagFlow.offset}`, { cache: "no-store" }).then(response => response.json());
      tagFlow.items = tagFlow.items.concat(body.items || []);
      tagFlow.hasMore = Boolean(body.has_more);
      tagFlow.offset = tagFlow.items.length;
    } catch (error) {
      tagFlow.error = error.message || "读取下一批题目失败。";
    }
    tagFlow.loading = false;
  }

  async function flowEnsureItem() {
    if (tagFlow.index < tagFlow.items.length) return;
    if (tagFlow.hasMore) {
      await flowLoadMore();
      if (tagFlow.index < tagFlow.items.length) return;
    }
    tagFlow.done = true;
  }

  function renderFlowQuestion(item) {
    const meta = tagFlow.dialog.querySelector(".tag-flow-meta");
    const stem = tagFlow.dialog.querySelector(".tag-flow-stem");
    const current = tagFlow.dialog.querySelector("#tagFlowCurrent");
    meta.replaceChildren();
    stem.replaceChildren();
    current.replaceChildren();
    if (!item) {
      const done = tagFlow.error ? "这一批读不出来" : "这一批贴完了";
      const panel = node("div", "tag-flow-done");
      panel.append(node("strong", "", done),
        node("p", "", tagFlow.error ? tagFlow.error : "这一批没有待贴标签的题了。题库里还有别的题时，重新进来会接着排。"));
      const again = node("button", "button primary", "再检查一遍"); again.type = "button";
      again.addEventListener("click", () => void openTagFlow());
      const exit = node("button", "button button-quiet", "回到题库"); exit.type = "button";
      exit.addEventListener("click", () => closeTagFlow());
      panel.append(again, exit);
      stem.append(panel);
      return;
    }
    meta.append(node("span", "", `第 ${item.number ?? "?"} 题`));
    if (item.source_filename) meta.append(node("small", "", String(item.source_filename)));
    const paper = node("div", "paper tag-flow-paper");
    QB.renderQuestion(paper, item.content || {}, { showNumber: false, showAnswer: "none" });
    stem.append(paper);
    const links = (item.tag_assignments || []).filter(link => !link.needs_review);
    if (links.length) {
      const label = node("small", "tag-flow-current-label", "已有标签");
      const chips = node("div", "tag-flow-chips");
      links.forEach(link => chips.append(node("span", "tag-flow-chip", link.tag?.name || "标签")));
      current.append(label, chips);
    } else {
      current.append(node("small", "tag-flow-current-label", "还没有知识点标签"));
    }
  }

  function paintFlowResults() {
    const results = tagFlow.dialog?.querySelector("#tagFlowResults");
    if (!results) return;
    [...results.children].forEach((row, index) => {
      row.classList.toggle("active", index === tagFlow.active);
      row.setAttribute("aria-selected", String(index === tagFlow.active));
    });
    const active = results.children[tagFlow.active];
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function renderFlowResults() {
    const results = tagFlow.dialog.querySelector("#tagFlowResults");
    results.replaceChildren();
    if (!tagFlow.results.length) {
      results.append(node("p", "tag-flow-empty", tagFlow.dialog.querySelector("#tagFlowSearch").value.trim()
        ? "没有找到这个标签。可以换个名字，或到标签管理里新建。"
        : "输入标签名开始搜索；↑↓ 选，回车贴上。"));
      return;
    }
    tagFlow.results.forEach((tag, index) => {
      const row = node("button", "tag-flow-result");
      row.type = "button"; row.dataset.tagId = tag.id;
      row.setAttribute("role", "option");
      const name = node("span", "tag-flow-result-name", tag.name);
      const path = flowTagPath(tag);
      const meta = node("small", "tag-flow-result-path", path || (tag.kind === "method" ? "解题方法" : tag.kind === "custom" ? "自定义标签" : ""));
      row.append(name, meta);
      row.addEventListener("click", () => void applyFlowTag(tag));
      row.addEventListener("pointerenter", () => { tagFlow.active = index; paintFlowResults(); });
      results.append(row);
    });
    paintFlowResults();
  }

  function renderFlowSuggest(item) {
    const box = tagFlow.dialog?.querySelector("#tagFlowSuggest");
    if (!box) return;
    box.replaceChildren();
    const suggestion = item?.tag_suggestion;
    if (!suggestion || suggestion.status !== "pending") { box.hidden = true; return; }
    const entries = (suggestion.suggestions || []).filter(entry => !entry.decision && (entry.tag_id || entry.name));
    if (!entries.length) { box.hidden = true; return; }
    box.hidden = false;
    box.append(node("small", "tag-flow-suggest-label", "AI 建议"));
    const chips = node("div", "tag-flow-chips");
    entries.slice(0, 4).forEach(entry => {
      const tag = entry.tag_id ? flowTagById(entry.tag_id) : null;
      const name = entry.name || entry.new_name || "新标签";
      const chip = node("button", "tag-flow-chip is-button", name);
      chip.type = "button";
      if (tag) {
        chip.title = `采纳这条建议并贴上「${tag.name}」`;
        chip.addEventListener("click", () => void applyFlowSuggestion(entry, (suggestion.suggestions || []).indexOf(entry)));
      } else {
        chip.disabled = true;
        chip.title = `AI 建议新建「${name}」；这条流水线里先不新建标签，可到「AI 建议待核对」里处理。`;
      }
      chips.append(chip);
    });
    box.append(chips);
  }

  function renderFlowRecent() {
    const box = tagFlow.dialog.querySelector("#tagFlowRecent");
    box.replaceChildren();
    const tags = flowRecentTags();
    if (!tags.length) return;
    box.append(node("small", "tag-flow-recent-label", "最近用过"));
    const chips = node("div", "tag-flow-chips");
    tags.forEach(tag => {
      const chip = node("button", "tag-flow-chip is-button", tag.name); chip.type = "button";
      chip.addEventListener("click", () => void applyFlowTag(tag));
      chips.append(chip);
    });
    box.append(chips);
  }

  function renderTagFlow() {
    if (!tagFlow.dialog) return;
    const progress = tagFlow.dialog.querySelector("#tagFlowProgress");
    const undo = tagFlow.dialog.querySelector("#tagFlowUndo");
    const item = flowCurrent();
    undo.hidden = !tagFlow.undoId;
    if (tagFlow.loading) {
      progress.textContent = "正在读取题目…";
      renderFlowQuestion(null);
    } else if (!item) {
      progress.textContent = "这一批结束了";
      renderFlowQuestion(null);
    } else {
      progress.textContent = `第 ${Math.min(tagFlow.index + 1, tagFlow.items.length)} 题 · 未标 ${tagFlow.total} 题`;
      renderFlowQuestion(item);
    }
    const input = tagFlow.dialog.querySelector("#tagFlowSearch");
    input.disabled = tagFlow.busy || !item;
    renderFlowSuggest(item);
    renderFlowResults();
    renderFlowRecent();
  }

  async function applyFlowSuggestion(entry, index) {
    const item = flowCurrent();
    const suggestion = item?.tag_suggestion;
    if (!item || !suggestion || tagFlow.busy || index == null || index < 0) return;
    tagFlow.busy = true;
    flowStatus(`正在采纳 AI 建议「${entry.name || entry.new_name || ""}」…`);
    try {
      const result = await tagRequest("/api/library/tag-suggestions/" + suggestion.id + "/resolve", {
        method: "POST",
        body: JSON.stringify({
          action: "accept", indices: [index], expected_revision: item.tag_revision,
          new_tags: {}, request_id: tagRequestId(),
        }),
      });
      item.tag_revision = result.revision ?? item.tag_revision;
      item.tag_suggestion = result.status === "pending" ? item.tag_suggestion : null;
      const tag = entry.tag_id ? flowTagById(entry.tag_id) : null;
      if (tag) rememberFlowTag(tag.id);
      tagFlow.undoId = result.operation_id || "";
      tagFlow.total = Math.max(0, tagFlow.total - 1);
      tagFlow.index += 1;
      tagFlow.results = []; tagFlow.active = 0;
      tagFlow.dialog.querySelector("#tagFlowSearch").value = "";
      flowStatus("已采纳 AI 建议并贴上");
      toast("已采纳 AI 建议并贴上", "success");
      await flowEnsureItem();
      renderTagFlow();
      tagFlow.dialog.querySelector("#tagFlowSearch").focus();
    } catch (error) {
      flowStatus(error.message || "建议没有贴上。", true);
      toast(error.message || "建议没有贴上。", "error");
    } finally {
      tagFlow.busy = false;
    }
  }

  async function applyFlowTag(tag) {
    const item = flowCurrent();
    if (!item || tagFlow.busy) return;
    tagFlow.busy = true;
    flowStatus(`正在贴上「${tag.name}」…`);
    try {
      const revision = Number(item.tag_revision) || 0;
      const payload = {
        publication_ids: [item.id], add: [tag.id], remove: [],
        scopes: { [tag.id]: { whole: true } },
        expected_revisions: { [item.id]: revision },
        request_id: tagRequestId(),
      };
      const result = await tagRequest("/api/library/tags/assignments/batch", { method: "POST", body: JSON.stringify(payload) });
      item.tag_revision = result.revisions?.[item.id] ?? revision + 1;
      item.tag_assignments = [...(item.tag_assignments || []), { tag, needs_review: false }];
      item.tags = [...(item.tags || []), tag.name];
      tagFlow.undoId = result.operation_id || "";
      rememberFlowTag(tag.id);
      tagFlow.total = Math.max(0, tagFlow.total - 1);
      tagFlow.index += 1;
      tagFlow.results = []; tagFlow.active = 0;
      tagFlow.dialog.querySelector("#tagFlowSearch").value = "";
      flowStatus(`已贴上「${tag.name}」`);
      toast(`已贴上「${tag.name}」`, "success");
      await flowEnsureItem();
      renderTagFlow();
      tagFlow.dialog.querySelector("#tagFlowSearch").focus();
    } catch (error) {
      flowStatus(error.message || "标签没有贴上。", true);
      toast(error.message || "标签没有贴上。", "error");
    } finally {
      tagFlow.busy = false;
    }
  }

  function skipFlowQuestion() {
    if (tagFlow.busy || !flowCurrent()) return;
    tagFlow.index += 1;
    tagFlow.results = []; tagFlow.active = 0;
    tagFlow.dialog.querySelector("#tagFlowSearch").value = "";
    flowStatus("已跳过这道题");
    void flowEnsureItem().then(() => renderTagFlow());
  }

  async function undoFlowTag() {
    if (!tagFlow.undoId || tagFlow.busy) return;
    const operationId = tagFlow.undoId;
    tagFlow.busy = true;
    try {
      await tagRequest("/api/library/tags/undo", { method: "POST", body: JSON.stringify({ operation_id: operationId }) });
      tagFlow.undoId = "";
      tagFlow.total += 1;
      flowStatus("已撤销上一次贴上");
      toast("已撤销上一次贴上", "success");
      renderTagFlow();
    } catch (error) {
      flowStatus(error.message || "撤销失败。", true);
    } finally {
      tagFlow.busy = false;
    }
  }


  if (state.document) ui.source.value = state.document;
  saveBasket();
  applyDirectoryWidth();
  load();
  // 1.25.58：目录默认开在左栏；关过一次就记住不再自动开。
  try { if (localStorage.getItem("qb-library-directory") !== "0") void directoryToggle(true); } catch (_) { /* 记不住就不自动开 */ }
  // 1.25.64：拉一份目录进 tagCatalogue，让筛选条、悬停都能拿到每条标签的颜色。
  // 目录本身已经各自 fetch 了一份，这里只为了把同一份数据放进 tag picker / 筛选条用的全局缓存。
  void loadTagCatalogue().catch(() => { /* 失败也不影响题库主功能 */ });
  // 1.25.71：开关的点击事件一开始就挂上，不等首次打开。原先写在 directoryEnsure 里，
  // 上一次关掉后 localStorage 是 "0"，init 不会调 directoryToggle，监听也就没绑上 —
  // 再点「目录」没反应。放在初始化末尾、测试片段会忽略的位置里。
  $("libraryDirectoryToggle")?.addEventListener("click", () => void directoryToggle());
})();
