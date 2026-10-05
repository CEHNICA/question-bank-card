(() => {
  "use strict";

  const QB = window.QBRender;
  const workspace = window.LibraryWorkspace;
  const solutions = window.LibrarySolutions;
  const $ = (id) => document.getElementById(id);
  const ui = {
    search: $("searchInput"), source: $("sourceSelect"), types: $("typeFilters"),
    status: $("libraryStatus"), list: $("libraryList"), more: $("moreButton"),
    basketButton: $("basketButton"), basketCount: $("basketCount"),
    sourceDialog: $("sourceDialog"), sourceTitle: $("sourceTitle"), sourcePages: $("sourcePages"), sourceReviewLink: $("sourceReviewLink"),
    sourceQuestion: $("sourceQuestion"), sourceWholePage: $("sourceWholePage"), sourceZoomOut: $("sourceZoomOut"),
    sourceZoomIn: $("sourceZoomIn"), sourceZoom: $("sourceZoom"), sourceFit: $("sourceFit"), sourceRelated: $("sourceRelated"),
    sheet: $("printSheet"), paper: $("printPaper"), printTitle: $("printTitle"), printAnswers: $("printAnswers"),
    printOrigin: $("printOrigin"), printAi: $("printAiAnswers"), printAiBox: $("printAiBox"),
    answerFilters: $("answerFilters"), tagBox: $("tagFilterBox"), tagSelect: $("tagSelect"), extraTools: $("extraTools")
  };
  const params = new URL(window.location.href).searchParams;
  const state = {
    q: params.get("q") || "",
    document: params.get("document") || "",
    type: params.get("type") || "",
    // human = 人对照原卷核对过；ai = AI 助手审核入库，还没人工核对。
    review: ["human", "ai"].includes(params.get("review")) ? params.get("review") : "",
    answer: ["yes", "no"].includes(params.get("answer")) ? params.get("answer") : "",
    tag: params.get("tag") || "",
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
    items: [],
    total: 0,
    facets: null,
    loading: false,
    token: 0,
    basket: loadBasket()
  };

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
      ["answer", state.answer], ["tag", state.tag]]) {
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
    for (const key of ["q", "document", "type", "review", "answer", "tag"]) {
      const value = key === "q" ? String(filters[key] || "").trim() : filters[key];
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
    if (!append && !quiet) ui.status.textContent = "正在读取题库…";
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
      state.features = body.features || {};
      state.ai = body.ai || { mode: "assistant" };
      state.items = append ? state.items.concat(body.items) : body.items;
      markLibraryResult(query);
      state.items.forEach((item) => state.catalog.set(item.id, item));
      render();
      // 助手任务只等待工具写回；API 任务由后台处理。两种结果都刷新显示。
      scheduleJobRefresh();
    } catch (error) {
      if (token === state.token) {
        ui.status.textContent = state.items.length ? "当前显示上一次读取的结果，尚未应用这次筛选。" : "题库尚未读取成功。";
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
      .filter((job) => job.kind === "tags" ? state.features.knowledge_tags : state.features.ai_answer);
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
    const typeKeys = new Set([...Object.keys(facets.types), ...(state.type ? [state.type] : [])]);
    [["", "全部题型", total], ...[...typeKeys].map((key) => [key, QB.TYPE_NAMES[key] || key, facets.types[key] || 0])]
      .forEach(([key, label, count]) => {
        const button = node("button", `draft-filter${state.type === key ? " active" : ""}`);
        button.append(node("span", "", label), node("span", "count", count));
        button.type = "button";
        button.setAttribute("aria-pressed", String(state.type === key));
        button.addEventListener("click", () => { state.type = key; syncUrl(); load(); });
        ui.types.append(button);
      });
    renderReviewFilters(facets.reviews || {});
    renderAnswerFilters(facets.answers || {});
    renderTagFilter(facets.tags || []);
    renderExtraTools();
    // 四个分组全空时，「更多筛选」点开了也是一片空白 —— 那就不出现。
    $("advancedFilters").hidden = [ui.answerFilters, $("reviewFilters"), ui.tagBox, ui.extraTools]
      .every((box) => box.hidden);
    const selectedView = state.view === "selected";
    for (const control of [ui.search, ui.source, $("sortSelect"), ...document.querySelectorAll("#typeFilters button, .library-advanced button, .library-advanced select")]) control.disabled = selectedView;
  }

  // 有没有答案：挑出原卷没给答案的题（补答案或做 AI 参考答案时用）。
  function renderAnswerFilters(answers) {
    const box = ui.answerFilters;
    const yes = Number(answers.yes) || 0;
    const no = Number(answers.no) || 0;
    box.hidden = !no && !state.answer;
    box.replaceChildren();
    if (box.hidden) return;
    [["", "全部", yes + no], ["yes", "有答案", yes], ["no", "无答案", no]].forEach(([key, label, count]) => {
      const button = node("button", `draft-filter${state.answer === key ? " active" : ""}`);
      button.append(node("span", "", label), node("span", "count", count));
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.answer === key));
      button.addEventListener("click", () => { state.answer = key; syncUrl(); load(); });
      box.append(button);
    });
  }

  function renderTagFilter(tags) {
    ui.tagBox.hidden = !(state.features.knowledge_tags || tags.length || state.tag);
    if (ui.tagBox.hidden) return;
    ui.tagSelect.replaceChildren(new Option(tags.length ? "全部知识点" : "还没有知识点标签", ""));
    tags.forEach((item) => ui.tagSelect.append(new Option(`${item.tag}（${item.count}）`, item.tag)));
    if (state.tag && !tags.some((item) => item.tag === state.tag)) ui.tagSelect.append(new Option(state.tag, state.tag));
    ui.tagSelect.value = state.tag;
  }

  // 设置里打开了“知识点标签”“AI 参考答案”才出现：一次给所有缺的题排队。
  function renderExtraTools() {
    const box = ui.extraTools;
    box.replaceChildren();
    const tools = [];
    if (state.features.knowledge_tags) {
      const tags = iconButton("button", "button button-quiet button-small", "给还没有知识点的题打标签", "plus");
      tags.addEventListener("click", () => queueJobs("tags", { missing: true }));
      tools.push(tags);
    }
    if (state.features.ai_answer) {
      const answers = iconButton("button", "button button-quiet button-small", "给原卷没答案的题做 AI 参考答案", "plus");
      answers.addEventListener("click", () => queueJobs("answer", { missing: true }));
      tools.push(answers);
    }
    box.hidden = !tools.length;
    if (tools.length) {
      if (state.ai.mode === "api" && state.ai.api_ready === true) box.append(node("span", "helper", "由已配置的 API 生成，每道题一次调用，会用到服务额度："), ...tools);
      else box.append(node("span", "helper", "请到“设置 → 服务与密钥”配置并测试答题 API。"));
    }
  }

  async function queueJobs(kind, target) {
    try {
      const configurationResponse = await fetch("/api/settings/library-ai", { cache: "no-store" });
      const configuration = await configurationResponse.json();
      if (!configurationResponse.ok || configuration.mode !== "api" || configuration.api_ready !== true) throw new Error("请到“设置 → 服务与密钥”配置并测试答题 API 后再生成。");
      const response = await fetch("/api/library/jobs", {
        method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
        body: JSON.stringify({ kind, ...target, api_only: true })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "没能排上队");
      const what = kind === "tags" ? "打知识点标签" : "做 AI 参考答案";
      const skipped = body.skipped ? `；${body.skipped} 道已有结果或当前不能生成` : "";
      toast(body.queued ? `已排队${what}：${body.queued} 道题，做好会自动显示${skipped}`
        : `没有需要${what}的题${skipped}`, body.queued ? "success" : "");
      load({ quiet: true });
    } catch (error) {
      toast(`${error.message || "没能排上队"} 请到“设置 → 服务与密钥”检查服务商、密钥和模型。`, "error");
    }
  }

  // 只在题库里有 AI 审核入库的题时出现：可以只看人工核对过的，或把 AI 审核的挑出来抽查。
  function renderReviewFilters(reviews) {
    const box = $("reviewFilters");
    const ai = Number(reviews.ai) || 0;
    const human = Number(reviews.human) || 0;
    box.hidden = !ai && !state.review;
    box.replaceChildren();
    if (box.hidden) return;
    [["", "全部", ai + human], ["human", "人工核对", human], ["ai", "AI 审核", ai]].forEach(([key, label, count]) => {
      const button = node("button", `draft-filter${state.review === key ? " active" : ""}`);
      button.append(node("span", "", label), node("span", "count", count));
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.review === key));
      if (key === "ai") button.title = "AI 助手审核后入库、还没人工核对的题";
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
    const selected = state.view === "selected", items = visibleItems().slice();
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
    open.open = false;
    open.querySelector("summary")?.focus({ preventScroll: true });
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

  function card(item) {
    const article = node("article", `library-card${state.basket.includes(item.id) ? " in-basket" : ""}`);
    article.id = `q-${item.id}`;
    const meta = node("header", "library-card-meta");
    const select = node("input", "library-card-select");
    select.type = "checkbox";
    select.checked = state.selected.has(item.id);
    select.setAttribute("aria-label", `勾选${item.source_filename}第 ${item.number} 题`);
    select.addEventListener("change", () => {
      if (select.checked) state.selected.add(item.id); else state.selected.delete(item.id);
      article.classList.toggle("is-selected", select.checked);
      syncSelection();
    });
    meta.append(select);
    meta.append(
      node("span", "library-source", item.source_filename),
      node("span", "", `原卷第 ${item.number} 题`),
      typeChip(item)
    );
    // 1.12.6：同一份卷被录过两次时，两份来源在题库里同名，只看文件名分不出
    // 哪份是哪份。补上录入时间，撤回一批时才不会撤错那一份。
    if (duplicateSourceNames().has(item.source_filename)) {
      const when = node("span", "library-note quiet", `录于 ${shortDate(sourceFirstSeen(item.document_id, item.published_at))}`);
      when.title = `这份来源的入库时间：${fullDate(sourceFirstSeen(item.document_id, item.published_at))}`;
      meta.append(when);
    }
    if (item.content?.body_mode === "source_image") meta.append(node("span", "library-note", "原图题"));
    if (item.origin) {
      const origin = node("span", "library-origin", `题源：${item.origin}`);
      origin.title = "题源：题干前印的出处。组卷打印时默认不印";
      meta.append(origin);
    }
    if (state.features.subquestions && item.subquestions >= 2) meta.append(node("span", "library-note", `含 ${item.subquestions} 小问`));
    if (solutions.hasContent(item.solution)) {
      meta.append(node("span", "library-note quiet", "已保存答案解析"));
    } else if (!item.has_answer) {
      const missing = node("span", "library-note quiet", item.ai_answer && state.features.ai_answer ? "原卷无答案 · 有 AI 参考" : "无答案");
      missing.title = "原卷没有给答案";
      meta.append(missing);
    }
    if (item.review?.source === "ai") {
      const badge = node("span", "library-review ai", `${item.review.agent || "AI"} 审核`);
      badge.title = "这道题是 AI 助手对照原卷后审核入库的，还没人工核对。回到题卡确认后会变成人工核对。";
      meta.append(badge);
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
          notes.join(" · ") + (hintOff ? "" : " · 点全屏看题查看")));
      }
    }
    const reveal = answerReveal(item);
    if (reveal) paper.append(reveal);
    const extras = extrasNode(item);
    if (extras) paper.append(extras);
    const actions = node("footer", "library-card-actions");
    const origin = iconButton("button", "button button-quiet button-small", "查看出处", "source");
    origin.addEventListener("click", () => openSource(item));
    const full = node("button", "button button-small library-full-button", "全屏看题");
    full.type = "button";
    full.addEventListener("click", () => openQuestionViewer(item, full));
    const expand = node("button", "button button-quiet button-small", expanded ? "收起题面" : "展开题面");
    expand.type = "button";
    expand.setAttribute("aria-expanded", String(expanded));
    expand.addEventListener("click", () => {
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
    editAnswer.addEventListener("click", () => { closeMenu(); openAnswerEditor([item], { scope: "library" }); });
    const editQuestion = node("button", "button button-quiet button-small", "修改题目");
    editQuestion.type = "button"; editQuestion.addEventListener("click", () => { closeMenu(); openQuestionEditor(item); });
    menu.append(editQuestion, editAnswer, review, withdraw, ...jobButtons(item));
    more.append(menu);
    // 原生 details 各开各的。开一个就把别的收起来，页面上不会同时挂好几个小窗口。
    more.addEventListener("toggle", () => { if (more.open) closeOtherCardMenus(more); });
    actions.append(origin, full, expand, more, node("span", "actions-spacer"), basket);
    article.append(meta, paper, actions);
    if (state.focus === item.id) article.classList.add("focused");
    return article;
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

  // 改知识点标签。标签打错以前唯一的出路是撤回重录；标签只是筛选用的分类，
  // 不是题面的一部分，所以这里只写 extras，不新建版本、不重新审核。
  let tagDialog = null, tagTarget = null, tagCatalogue = [], tagPicked = [];

  function tagEndpoint(item) { return `/api/library/${encodeURIComponent(item.id)}/tags`; }

  function ensureTagDialog() {
    if (tagDialog) return tagDialog;
    tagDialog = node("dialog", "tag-editor-dialog");
    tagDialog.id = "tagEditorDialog";
    tagDialog.setAttribute("aria-labelledby", "tagEditorTitle");
    const bar = node("header", "tag-editor-bar");
    const heading = node("div", "tag-editor-heading");
    const title = node("h3", "", "知识点标签"); title.id = "tagEditorTitle";
    const place = node("p", "tag-editor-place");
    heading.append(title, place);
    const close = node("button", "button button-quiet", "关闭");
    close.type = "button";
    close.addEventListener("click", () => tagDialog.close());
    bar.append(heading, node("span", "tag-editor-spacer"), close);
    const body = node("div", "tag-editor-body");
    const search = node("input", "tag-editor-search");
    search.type = "search";
    search.placeholder = "搜知识点，例如 函数";
    search.setAttribute("aria-label", "在知识点目录里搜索");
    search.id = "tagEditorSearch";
    search.addEventListener("input", renderTagList);
    const picked = node("div", "tag-editor-picked");
    picked.id = "tagEditorPicked";
    const list = node("div", "tag-editor-list");
    list.id = "tagEditorList";
    list.setAttribute("aria-label", "知识点目录");
    body.append(search, picked, list);
    const note = node("p", "helper tag-editor-note", "标签只用于筛选和搜索，不改动题面。清空之后这道题可以重新自动生成标签。");
    const foot = node("footer", "tag-editor-foot");
    const clear = node("button", "button button-quiet", "清除全部标签");
    clear.type = "button";
    clear.id = "tagEditorClear";
    clear.addEventListener("click", () => { tagPicked = []; renderTagPicked(); renderTagList(); });
    const cancel = node("button", "button", "取消");
    cancel.type = "button";
    cancel.id = "tagEditorCancel";
    cancel.addEventListener("click", () => tagDialog.close());
    const save = node("button", "button button-primary", "保存标签");
    save.type = "button";
    save.id = "tagEditorSave";
    save.addEventListener("click", saveTags);
    foot.append(clear, node("span", "tag-editor-spacer"), cancel, save);
    tagDialog.append(bar, body, note, foot);
    document.body.append(tagDialog);
    return tagDialog;
  }

  function renderTagPicked() {
    const box = $("tagEditorPicked");
    if (!box) return;
    const limit = tagDialog?.dataset.max || "3";
    box.replaceChildren(node("span", "tag-editor-picked-label", `已选 ${tagPicked.length} / ${limit}`));
    // 按存下来的顺序显示，不是按目录顺序：自动打的标签是有先后的，
    // 打开对话框又原样关掉，不该把顺序改掉。
    tagPicked.forEach((point) => {
      const chip = node("button", "library-tag active", point);
      chip.type = "button";
      chip.title = "去掉这个知识点";
      chip.addEventListener("click", () => { tagPicked = tagPicked.filter((value) => value !== point); renderTagPicked(); renderTagList(); });
      box.append(chip);
    });
    if (!tagPicked.length) box.append(node("span", "tag-editor-empty", "还没有选。保存空的就等于清除标签。"));
  }

  function renderTagList() {
    const list = $("tagEditorList");
    if (!list) return;
    const keyword = String($("tagEditorSearch")?.value || "").trim();
    // 章名也一起搜：目录里搜“圆锥”要能找到椭圆、双曲线、抛物线，
    // 可这三个词本身都不含“圆锥”，只在它们所属的章名里。
    const shown = keyword ? tagCatalogue.filter((item) => item.point.includes(keyword) || (item.chapter || "").includes(keyword)) : tagCatalogue;
    list.replaceChildren();
    if (!shown.length) {
      list.append(node("p", "library-empty", keyword ? `目录里没有含“${keyword}”的知识点。` : "知识点目录是空的。"));
      return;
    }
    let chapter = null;
    shown.forEach((item) => {
      if (item.chapter && item.chapter !== chapter) {
        chapter = item.chapter;
        list.append(node("p", "tag-editor-chapter", chapter));
      }
      const on = tagPicked.includes(item.point);
      const chip = node("button", `tag-editor-point${on ? " active" : ""}`, item.point);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(on));
      chip.title = on ? "取消这个知识点" : "加上这个知识点";
      chip.addEventListener("click", () => {
        if (on) tagPicked = tagPicked.filter((value) => value !== item.point);
        else if (tagPicked.length >= Number(tagDialog.dataset.max || 3)) { toast(`一道题最多 ${tagDialog.dataset.max} 个知识点`, "error"); return; }
        else tagPicked = [...tagPicked, item.point];
        renderTagPicked();
        renderTagList();
      });
      list.append(chip);
    });
  }

  async function openTagEditor(item) {
    const dialog = ensureTagDialog();
    tagTarget = item;
    tagDialog.dataset.max = "3";
    $("tagEditorSearch").value = "";
    tagDialog.classList.add("loading");
    try {
      const response = await fetch(tagEndpoint(item), { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "打不开知识点目录");
      tagCatalogue = body.catalogue || [];
      tagDialog.dataset.max = String(body.max || 3);
      tagPicked = [...(body.tags || [])];
      dialog.querySelector(".tag-editor-place").textContent =
        `${item.source_filename} · 第 ${item.number} 题${body.source === "human" ? " · 当前是人工改过的" : body.source ? ` · 当前由 ${body.source} 自动生成` : ""}`;
      renderTagPicked();
      renderTagList();
      if (!dialog.open) dialog.showModal();
      $("tagEditorSearch").focus();
    } catch (error) {
      toast(`${error.message || "打不开知识点目录"} 稍后再试。`, "error");
    } finally {
      tagDialog.classList.remove("loading");
    }
  }

  async function saveTags() {
    if (!tagTarget) return;
    const save = $("tagEditorSave");
    save.disabled = true;
    try {
      const response = await fetch(tagEndpoint(tagTarget), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
        body: JSON.stringify({ tags: tagPicked.slice() })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "这次保存没成功");
      tagDialog.close();
      toast(body.tags.length ? `已改成：${body.tags.join("、")}` : "标签已清空，这道题可以重新自动生成", "success");
      load();
    } catch (error) {
      toast(`${error.message || "这次保存没成功"} 标签没有改动。`, "error");
    } finally {
      save.disabled = false;
    }
  }

  // 知识点标签：不属于题面快照，单独显示。
  function extrasNode(item) {
    const box = node("div", "library-extras");
    if ((item.tags || []).length) {
      const row = node("div", "library-tags");
      row.append(node("span", "library-tags-label", "知识点"));
      item.tags.forEach((tag) => {
        const chip = node("button", `library-tag${state.tag === tag ? " active" : ""}`, tag);
        chip.type = "button";
        chip.title = state.tag === tag ? "取消按这个知识点筛选" : "只看这个知识点的题";
        chip.addEventListener("click", () => { state.tag = state.tag === tag ? "" : tag; syncUrl(); load(); });
        row.append(chip);
      });
      const edit = node("button", "library-tags-edit", "改");
      edit.type = "button";
      edit.id = "libraryTagEdit";
      edit.title = "改这道题的知识点标签：换掉、去掉，或清空后重新自动生成";
      edit.addEventListener("click", () => openTagEditor(item));
      row.append(edit);
      box.append(row);
    }
    Object.entries(item.job_errors || {})
      .filter(([kind]) => (kind === "tags" ? state.features.knowledge_tags : state.features.ai_answer))
      .forEach(([kind, message]) => box.append(node("p", "library-job-error",
        `${kind === "tags" ? "打知识点标签" : "AI 解答"}没做成：${message}。请到“设置 → 服务与密钥”检查服务商、密钥和模型。`)));
    return box.childNodes.length ? box : null;
  }

  function jobButtons(item) {
    const buttons = [];
    if (state.ai.mode !== "api" || state.ai.api_ready !== true) return buttons;
    const details = item.job_details || [];
    const waiting = new Set(details.length ? details.filter((job) => job.executor === state.ai.mode).map((job) => job.kind) : item.jobs || []);
    if (state.features.knowledge_tags && !(item.tags || []).length) {
      const busy = waiting.has("tags");
      const assistant = state.ai.mode === "assistant" && details.some((job) => job.kind === "tags" && job.executor === "assistant");
      const button = iconButton("button", "button button-quiet button-small", busy ? (assistant ? "待助手生成标签" : "正在打知识点标签…") : "打知识点标签", busy ? "" : "plus");
      button.disabled = busy;
      button.addEventListener("click", () => queueJobs("tags", { ids: [item.id] }));
      buttons.push(button);
    }
    if (state.features.ai_answer && !item.has_answer && !item.ai_answer) {
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
    renderFacets();
    const notice = $("assistantTaskNotice");
    notice.hidden = true;
    notice.textContent = "";
    renderActiveFilters();
    renderBasket();
    syncSelection();
    ui.list.replaceChildren();
    const shown = visibleItems();
    if (!shown.length) {
      const empty = node("div", "library-empty");
      const filtered = Boolean(state.q.trim() || state.document || state.type || state.review || state.answer || state.tag);
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
      const signature = JSON.stringify([item, state.basket.includes(item.id), state.features, state.ai.mode, state.selected.has(item.id), state.expanded.has(item.id), state.opened.has(item.id), state.tag]);
      const kept = previous.get(item.id);
      const node = kept && kept.signature === signature ? kept.node : card(item);
      state.cards.set(item.id, { signature, node });
      ui.list.append(node);
    });
    const currentQuestion = state.catalog.get(state.questionReturnId);
    if ($("questionDialog").open && currentQuestion && state.questionSignature !== questionSignature(currentQuestion)) openQuestion(currentQuestion);
    ui.status.textContent = state.view === "selected" ? `试题篮 ${state.basket.length} 题 · 已载入 ${shown.length} 题。已选题目不受搜索与筛选影响。`
      : state.total
      ? `已显示 ${state.items.length} / 共 ${state.total} 题${state.q ? `，匹配“${state.q}”` : ""} · 每题保留入库版本。`
      : "";
    ui.more.hidden = state.view === "selected" || state.items.length >= state.total;
    requestAnimationFrame(() => readingOverflowHints(ui.list));
    if (state.focus) {
      const target = document.getElementById(`q-${state.focus}`);
      if (target) {
        requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "center" }));
        state.focus = "";
      }
    }
  }

  // Optional guidance is checked where it is produced, so a re-render, a
  // question switch or a new window cannot bring a dismissed hint back.
  function hintHidden(category) {
    return Boolean(window.QBShortcutHelp?.hintDismissed?.(category));
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
    if (state.view !== "selected") return state.items;
    const missing = new Set(state.basketMissing.map((item) => item.id));
    return state.basket.filter((id) => !missing.has(id)).map((id) => state.catalog.get(id)).filter(Boolean);
  }

  function clearFilters() {
    window.clearTimeout(searchTimer);
    state.q = state.document = state.type = state.review = state.answer = state.tag = "";
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
      ["tag", state.tag ? `知识点：${state.tag}` : ""]];
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
      remove.addEventListener("click", () => { state[key] = ""; ui.search.value = state.q; syncUrl(); void load(); });
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
    $("allQuestionsButton").setAttribute("aria-pressed", String(state.view === "all"));
    $("allQuestionsButton").classList.toggle("active", state.view === "all");
    $("basketViewButton").setAttribute("aria-pressed", String(state.view === "selected"));
    $("basketViewButton").classList.toggle("active", state.view === "selected");
    const counts = {}, sources = new Set();
    const list = $("basketList");
    list.replaceChildren();
    const missing = new Map(state.basketMissing.map((item) => [item.id, item.reason]));
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
      const name = item ? `${item.source_filename} · 第 ${item.number} 题` : `选题 ${index + 1}`;
      open.append(node("strong", "", item ? `第 ${item.number} 题 · ${QB.TYPE_NAMES[item.question_type] || "其他"}` : `选题 ${index + 1}`), node("span", "", name));
      if (missing.has(id)) open.append(node("span", "basket-item-error", missing.get(id)));
      open.addEventListener("click", () => openQuestion(item));
      const remove = node("button", "button button-quiet button-small basket-remove", "移出");
      remove.type = "button";
      remove.setAttribute("aria-label", `移出${name}`);
      remove.addEventListener("click", () => { state.basket = state.basket.filter((value) => value !== id); saveBasket(); render(); });
      row.append(open, remove);
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
    $("questionTitle").textContent = `${item.source_filename} · 第 ${item.number} 题`;
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
      text: `“${item.source_filename} 第 ${item.number} 题”会从正式题库撤下。题卡会保留，可以重新入库。`,
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
      toast(`已撤回“${item.source_filename} 第 ${item.number} 题”`, "success");
      load();
    } catch (error) {
      ui.status.textContent = error.message || "撤回失败。";
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
      ui.status.textContent = error.message || "撤回失败。";
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
        name.append(node("strong", "", `${item.source_filename} · 第 ${item.number} 题`));
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
    ui.sourceTitle.textContent = `${item.source_filename} · 第 ${item.number} 题`;
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
  const printState = { token: 0, items: [], missing: [], loading: false, exporting: false, availableAnswers: 0, tooWide: 0, returnFocus: null,
    optionOverrides: {}, answerSpaceOverrides: {}, questionBreaks: [], activeToolId: null, layoutToken: 0, layoutPending: false, layoutError: "",
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
      question_breaks: Array.isArray(options.question_breaks) ? [...new Set(options.question_breaks.filter(id => typeof id === "string"))] : []
    };
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
      question_breaks: (printState.questionBreaks || []).filter(id => !state.basket || state.basket.includes(id))
    });
  }

  function prunePrintChoices() {
    const ids = new Set(state.basket);
    printState.optionOverrides = Object.fromEntries(Object.entries(printState.optionOverrides || {}).filter(([id]) => ids.has(id)));
    printState.answerSpaceOverrides = Object.fromEntries(Object.entries(printState.answerSpaceOverrides || {}).filter(([id]) => ids.has(id)));
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
      if (known) printNames.set(id, `${known.source_filename || "试卷"} · 第 ${known.number} 题`);
    });
    try {
      // Revalidate every selected snapshot, including cards already displayed.
      // An old cached item must not print after it was withdrawn or superseded.
      const body = await batchItems(ids);
      const byId = new Map(body.items.map((item) => [item.id, item]));
      items = ids.map((id) => byId.get(id)).filter(Boolean).map(item => ({ ...item }));
      await resolvePrintSolutions(items);
      items.forEach((item) => { state.catalog.set(item.id, byId.get(item.id)); printNames.set(item.id, `${item.source_filename || "试卷"} · 第 ${item.number} 题`); });
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

  function renderPrintMissing() {
    const box = $("printMissing");
    box.replaceChildren();
    box.hidden = !printState.missing.length;
    if (box.hidden) return;
    box.append(node("p", "", `试题篮 ${state.basket.length} 题，成功载入 ${printState.items.length} 题；以下 ${printState.missing.length} 题尚未载入。处理后才能导出或打印。`));
    printState.missing.forEach((item) => {
      const row = node("div", "print-missing-row");
      const remove = node("button", "button button-small", "移出试题篮");
      remove.type = "button";
      remove.setAttribute("aria-label", `移出${item.label}`);
      remove.addEventListener("click", () => {
        state.basket = state.basket.filter((id) => id !== item.id);
        printState.missing = printState.missing.filter((row) => row.id !== item.id);
        saveBasket();
        renderPrint(printState.items);
        render();
        ($("retryPrintMissing") || $("closePrint")).focus();
      });
      row.append(node("span", "", `${item.label}：${item.reason}`), remove);
      box.append(row);
    });
    const retry = node("button", "button button-small", "重试未载入题目");
    retry.id = "retryPrintMissing";
    retry.type = "button";
    retry.addEventListener("click", openPrint);
    box.append(retry);
  }

  function renderPrint(items) {
    // Any new source, including an empty basket, invalidates pending pages.
    ++printState.layoutToken;
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
    const groups = [["single_choice", "选择题"], ["multiple_choice", "多选题"], ["fill_blank", "填空题"], ["true_false", "判断题"], ["free_response", "解答题"]];
    const known = new Set(groups.map(([key]) => key));
    const ordered = [];
    groups.forEach(([key, name]) => {
      const group = items.filter((item) => item.question_type === key);
      if (group.length) ordered.push([name, group]);
    });
    const others = items.filter((item) => !known.has(item.question_type));
    if (others.length) ordered.push(["其他", others]);
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
          if (ui.printOrigin.checked && origin) block.querySelector(".qb-stem-body")?.prepend(node("span", "print-origin", `（${origin}）`));
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
      preparePrintLayout();
      const layoutHost = node("div", "print-paper");
      const result = await window.ExamLayout.paginate(flow, { host: layoutHost, ...currentPrintOptions() });
      if (token !== printState.layoutToken || !ui.sheet.open) return;
      ui.paper.replaceChildren(...result.pages);
      ui.paper.dataset.pageCount = String(result.page_count);
      window.ExamLayout.scale(ui.paper);
      const seen = new Set();
      const knownTypes = new Set(["single_choice", "multiple_choice", "fill_blank", "true_false", "free_response"]);
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
    const tooWide = ui.paper.querySelectorAll('.katex[data-exam-math-overflow="1"]').length;
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
      const block = Array.from(ui.paper.querySelectorAll(".print-question"))
        .find((question) => question.dataset.questionId === item.id);
      const preferred = block?.querySelector(`[data-action="${step < 0 ? "up" : "down"}"]`);
      const focus = preferred && !preferred.disabled ? preferred : block?.querySelector("button:not(:disabled)");
      focus?.focus({ preventScroll: true });
      block?.scrollIntoView({ block: "nearest" });
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
      const next = ui.paper.querySelectorAll(".print-question-tools > summary")[Math.min(number - 1, items.length - 1)];
      (next || $("closePrint")).focus({ preventScroll: true });
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
  ui.tagSelect.addEventListener("change", () => { state.tag = ui.tagSelect.value; syncUrl(); load(); });
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
    }
    document.body.classList.toggle("rail-open", open);
    railScrim.hidden = !open;
    railToggle.setAttribute("aria-expanded", String(open));
    if (open) document.querySelector(".library-rail .library-search input")?.focus({ preventScroll: true });
  };
  railToggle.addEventListener("click", () => setRail(!document.body.classList.contains("rail-open")));
  railScrim.addEventListener("click", () => setRail(false));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !document.body.classList.contains("rail-open")) return;
    // 和抽屉同一规矩：原生 <dialog> 在 top layer，永远盖住侧栏，Esc 先让给它。
    if (document.querySelector("dialog[open]")) return;
    event.preventDefault();
    setRail(false);
    railToggle.focus({ preventScroll: true });
  });
  $("libraryKeysButton").addEventListener("click", () => window.QBShortcutHelp?.open("library"));
  // 原来这里常驻两条提示条（题库快捷键、打印快捷键）外加一个「恢复操作提示」按钮。
  // 三样加起来是横在题目上方的一整条，眼睛得先扫过它才看得到题。提示内容都在
  // 设置里那一栏（快捷键按钮 / ? 键），这页不再挂常驻提示，也不再需要「恢复」——
  // 没有被这里关掉的东西，就没有东西要恢复。
  document.addEventListener("keydown", (event) => {
    const editable = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]';
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
