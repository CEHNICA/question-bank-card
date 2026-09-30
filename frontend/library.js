(() => {
  "use strict";

  const QB = window.QBRender;
  const $ = (id) => document.getElementById(id);
  const ui = {
    search: $("searchInput"), source: $("sourceSelect"), types: $("typeFilters"), answers: $("answerToggle"),
    status: $("libraryStatus"), list: $("libraryList"), more: $("moreButton"),
    basketButton: $("basketButton"), basketCount: $("basketCount"),
    sourceDialog: $("sourceDialog"), sourceTitle: $("sourceTitle"), sourcePages: $("sourcePages"), sourceReviewLink: $("sourceReviewLink"),
    sheet: $("printSheet"), paper: $("printPaper"), printTitle: $("printTitle"), printAnswers: $("printAnswers")
  };
  const params = new URL(window.location.href).searchParams;
  const state = {
    q: params.get("q") || "",
    document: params.get("document") || "",
    type: params.get("type") || "",
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

  function loadBasket() {
    try {
      const value = JSON.parse(window.localStorage.getItem("qb-basket") || "[]");
      return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
    } catch {
      return [];
    }
  }

  function saveBasket() {
    try { window.localStorage.setItem("qb-basket", JSON.stringify(state.basket)); } catch { /* 浏览器禁止存储时，试题篮只在本页有效 */ }
    ui.basketCount.textContent = String(state.basket.length);
    ui.basketButton.hidden = state.basket.length === 0;
    ui.basketButton.classList.remove("bump");
    void ui.basketButton.offsetWidth;
    ui.basketButton.classList.add("bump");
  }

  function syncUrl() {
    const url = new URL(window.location.href);
    for (const [key, value] of [["q", state.q], ["document", state.document], ["type", state.type]]) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    url.searchParams.delete("focus");
    window.history.replaceState(null, "", url);
  }

  async function load({ append = false } = {}) {
    const token = ++state.token;
    state.loading = true;
    const query = new URLSearchParams({ limit: "40", offset: String(append ? state.items.length : 0) });
    if (state.q.trim()) query.set("q", state.q.trim());
    if (state.document) query.set("document", state.document);
    if (state.type) query.set("type", state.type);
    if (!append) ui.status.textContent = "正在读取题库…";
    try {
      const response = await fetch(`/api/library?${query}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "读取题库失败");
      if (token !== state.token) return;
      state.total = body.total;
      state.facets = body.facets;
      state.items = append ? state.items.concat(body.items) : body.items;
      render();
    } catch (error) {
      if (token === state.token) ui.status.textContent = error.message || "读取题库失败。";
    } finally {
      if (token === state.token) state.loading = false;
    }
  }

  function renderFacets() {
    const facets = state.facets || { sources: [], types: {} };
    const current = ui.source.value || state.document;
    ui.source.replaceChildren(new Option("全部试卷", ""));
    facets.sources.forEach((source) => {
      ui.source.append(new Option(`${source.filename}（${source.count}）`, source.document_id || ""));
    });
    ui.source.value = current;
    if (ui.source.value !== current) ui.source.value = "";
    ui.types.replaceChildren();
    const total = Object.values(facets.types).reduce((sum, value) => sum + value, 0);
    [["", "全部题型", total], ...Object.entries(facets.types).map(([key, count]) => [key, QB.TYPE_NAMES[key] || key, count])]
      .forEach(([key, label, count]) => {
        const button = node("button", `draft-filter${state.type === key ? " active" : ""}`);
        button.append(node("span", "", label), node("span", "count", count));
        button.type = "button";
        button.setAttribute("aria-pressed", String(state.type === key));
        button.addEventListener("click", () => { state.type = key; syncUrl(); load(); });
        ui.types.append(button);
      });
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN");
  }

  function card(item) {
    const article = node("article", `library-card${state.basket.includes(item.id) ? " in-basket" : ""}`);
    article.id = `q-${item.id}`;
    const meta = node("header", "library-card-meta");
    meta.append(
      node("span", "library-source", item.source_filename),
      node("span", "", `原卷第 ${item.number} 题`),
      node("span", "library-type", QB.TYPE_NAMES[item.question_type] || item.question_type),
      node("span", "", `第 ${item.version} 版 · ${formatDate(item.published_at)} 入库`)
    );
    const paper = node("div", "paper");
    QB.renderQuestion(paper, item.content, { showNumber: false, showAnswer: ui.answers.checked ? "open" : "collapsed" });
    const actions = node("footer", "library-card-actions");
    const origin = iconButton("button", "button button-quiet button-small", "查看出处", "source");
    origin.addEventListener("click", () => openSource(item));
    const review = iconButton("a", "button button-quiet button-small", "回到题卡", "back");
    review.href = item.document_id ? `/?document=${encodeURIComponent(item.document_id)}${item.draft_id ? `&draft=${encodeURIComponent(item.draft_id)}` : ""}` : "/";
    const withdraw = iconButton("button", "button button-quiet button-small library-withdraw", "撤回", "undo");
    withdraw.title = "从正式题库撤下；题卡和历史版本都保留，可重新入库";
    withdraw.addEventListener("click", () => withdrawItem(item));
    const inBasket = state.basket.includes(item.id);
    const basket = iconButton("button", `button ${inBasket ? "button-outline" : ""} button-small`, inBasket ? "已在试题篮" : "加入试题篮", inBasket ? "check" : "plus");
    basket.setAttribute("aria-pressed", String(inBasket));
    basket.addEventListener("click", () => {
      state.basket = inBasket ? state.basket.filter((id) => id !== item.id) : state.basket.concat(item.id);
      saveBasket();
      article.replaceWith(card(item));
    });
    actions.append(origin, review, withdraw, node("span", "actions-spacer"), basket);
    article.append(meta, paper, actions);
    if (state.focus === item.id) article.classList.add("focused");
    return article;
  }

  function render() {
    renderFacets();
    ui.list.replaceChildren();
    if (!state.items.length) {
      const empty = node("div", "library-empty");
      empty.append(node("p", "", state.q || state.document || state.type ? "没有找到符合条件的题目。" : "题库还是空的。"),
        node("p", "helper", "在“录入终审”页核对并标记题卡通过后，点“入库”，题目就会出现在这里。"));
      ui.list.append(empty);
    }
    state.items.forEach((item) => ui.list.append(card(item)));
    ui.status.textContent = state.total
      ? `共 ${state.total} 道已入库题目${state.q ? `，匹配“${state.q}”` : ""}。每道题都是标记通过时的版本快照。`
      : "";
    ui.more.hidden = state.items.length >= state.total;
    if (state.focus) {
      const target = document.getElementById(`q-${state.focus}`);
      if (target) {
        requestAnimationFrame(() => target.scrollIntoView({ behavior: "smooth", block: "center" }));
        state.focus = "";
      }
    }
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
      saveBasket();
      toast(`已撤回“${item.source_filename} 第 ${item.number} 题”`, "success");
      load();
    } catch (error) {
      ui.status.textContent = error.message || "撤回失败。";
      toast(error.message || "撤回失败", "error");
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

  function openSource(item) {
    const content = item.content || {};
    ui.sourceTitle.textContent = `${item.source_filename} · 第 ${item.number} 题`;
    ui.sourceReviewLink.href = item.document_id ? `/?document=${encodeURIComponent(item.document_id)}${item.draft_id ? `&draft=${encodeURIComponent(item.draft_id)}` : ""}` : "/";
    ui.sourceReviewLink.hidden = !item.document_id;
    ui.sourcePages.replaceChildren();
    const regions = [
      ...(content.sources || []).filter((source) => source.type !== "image").map((source) => ({ ...source, kind: "text" })),
      ...(content.figures || []).map((figure) => ({ ...figure, kind: "figure" }))
    ].filter((region) => Array.isArray(region.bbox) && Number.isInteger(region.page_idx));
    if (!item.document_id || !regions.length) {
      ui.sourcePages.append(node("p", "helper", "这道题没有保存可定位的原卷坐标，或原试卷已从本机删除。"));
    }
    const pages = [...new Set(regions.map((region) => region.page_idx))].sort((a, b) => a - b);
    pages.forEach((page) => {
      const frame = node("figure", "source-page");
      const surface = node("div", "source-surface");
      const image = node("img");
      image.alt = `原卷第 ${page + 1} 页`;
      image.src = `/api/documents/${encodeURIComponent(item.document_id)}/pages/${page}/preview`;
      surface.append(image);
      regions.filter((region) => region.page_idx === page).forEach((region) => surface.append(box(region.bbox, region.kind)));
      frame.append(surface, node("figcaption", "", `第 ${page + 1} 页`));
      ui.sourcePages.append(frame);
      image.addEventListener("load", () => {
        const first = surface.querySelector(".source-box");
        if (first && pages[0] === page) first.scrollIntoView({ block: "center" });
      }, { once: true });
    });
    ui.sourceDialog.showModal();
  }

  // ---------------------------------------------------------------- 组卷

  async function openPrint() {
    ui.paper.replaceChildren(node("p", "helper", "正在准备…"));
    ui.sheet.hidden = false;
    document.body.classList.add("printing");
    const items = [];
    for (const id of state.basket) {
      const known = state.items.find((item) => item.id === id);
      if (known) { items.push(known); continue; }
      try {
        const response = await fetch(`/api/library/${encodeURIComponent(id)}`, { cache: "no-store" });
        if (response.ok) {
          const body = await response.json();
          if (body.publication?.status === "published") items.push(body.publication);
        }
      } catch { /* 已删除的题跳过 */ }
    }
    renderPrint(items);
  }

  function renderPrint(items) {
    ui.paper.replaceChildren();
    const title = node("h2", "print-title", ui.printTitle.value.trim() || "练习");
    const info = node("p", "print-info", "姓名 ____________　班级 ____________　得分 ________");
    ui.paper.append(title, info);
    if (!items.length) {
      ui.paper.append(node("p", "helper", "试题篮是空的。"));
      return;
    }
    const groups = [["single_choice", "选择题"], ["multiple_choice", "多选题"], ["fill_blank", "填空题"], ["free_response", "解答题"]];
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
      ui.paper.append(node("h3", "print-section", `${chinese[index] || index + 1}、${name}`));
      group.forEach((item, position) => {
        number += 1;
        const block = node("div", "print-question");
        QB.renderQuestion(block, item.content, { number, showAnswer: "none" });
        block.append(printTools(items, group, position));
        ui.paper.append(block);
        answers.push([number, item]);
      });
    });
    if (ui.printAnswers.checked) {
      const key = node("section", "print-answers");
      key.append(node("h3", "print-section", "参考答案与解析"));
      answers.forEach(([index, item]) => {
        const row = node("div", "print-answer-row");
        row.append(node("strong", "", `${index}.`));
        const body = node("div");
        const answer = node("span");
        if (/^\s*[A-D]{1,4}\s*$/.test(String(item.content.answer ?? ""))) answer.textContent = String(item.content.answer).trim();
        else QB.renderTypeset(answer, item.content.answer, { empty: "（原卷未提供答案）" });
        body.append(answer);
        if (String(item.content.analysis || "").trim()) {
          const analysis = node("div", "qb-analysis");
          QB.renderTypeset(analysis, item.content.analysis);
          body.append(analysis);
        }
        row.append(body);
        key.append(row);
      });
      ui.paper.append(key);
    }
    ui.paper.append(node("p", "print-footer", `共 ${number} 题 · 题目来自本机正式题库，均为题卡审核页中已标记通过的版本；正式使用前请按场景复核`));
  }

  // Move up / down within the same section, or take the question out of the
  // basket, without leaving the preview.  Hidden when printing.
  function printTools(items, group, position) {
    const tools = node("div", "print-question-tools no-print");
    const item = group[position];
    const move = (step) => {
      const other = group[position + step];
      if (!other) return;
      const from = state.basket.indexOf(item.id);
      const to = state.basket.indexOf(other.id);
      if (from < 0 || to < 0) return;
      [state.basket[from], state.basket[to]] = [state.basket[to], state.basket[from]];
      saveBasket();
      const a = items.indexOf(item);
      const b = items.indexOf(other);
      [items[a], items[b]] = [items[b], items[a]];
      renderPrint(items);
    };
    const up = node("button", "", "↑");
    up.type = "button";
    up.title = "上移";
    up.disabled = position === 0;
    up.addEventListener("click", () => move(-1));
    const down = node("button", "", "↓");
    down.type = "button";
    down.title = "下移";
    down.disabled = position === group.length - 1;
    down.addEventListener("click", () => move(1));
    const remove = node("button", "remove", "×");
    remove.type = "button";
    remove.title = "移出试题篮";
    remove.addEventListener("click", () => {
      state.basket = state.basket.filter((id) => id !== item.id);
      saveBasket();
      items.splice(items.indexOf(item), 1);
      renderPrint(items);
      render();
    });
    tools.append(up, down, remove);
    return tools;
  }

  function closePrint() {
    ui.sheet.hidden = true;
    document.body.classList.remove("printing");
  }

  // ---------------------------------------------------------------- 事件

  let searchTimer = null;
  ui.search.value = state.q;
  ui.search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => { state.q = ui.search.value; syncUrl(); load(); }, 250);
  });
  ui.source.addEventListener("change", () => { state.document = ui.source.value; syncUrl(); load(); });
  ui.answers.addEventListener("change", render);
  ui.more.addEventListener("click", () => load({ append: true }));
  ui.basketButton.addEventListener("click", openPrint);
  $("printButton").addEventListener("click", () => window.print());
  $("closePrint").addEventListener("click", closePrint);
  $("clearBasket").addEventListener("click", () => { state.basket = []; saveBasket(); closePrint(); render(); });
  ui.printTitle.addEventListener("input", () => { const title = ui.paper.querySelector(".print-title"); if (title) title.textContent = ui.printTitle.value.trim() || "练习"; });
  ui.printAnswers.addEventListener("change", openPrint);
  ui.sourceDialog.addEventListener("click", (event) => {
    if (event.target === ui.sourceDialog || event.target.closest("[data-close]")) ui.sourceDialog.close();
  });
  $("confirmDialog").addEventListener("click", (event) => {
    if (event.target === $("confirmDialog")) $("confirmDialog").close();
  });
  const toolbar = document.querySelector(".library-toolbar");
  const syncToolbar = () => {
    const top = document.querySelector(".topbar")?.offsetHeight || 56;
    toolbar.classList.toggle("stuck", window.scrollY > 0 && toolbar.getBoundingClientRect().top <= top + 1);
  };
  window.addEventListener("scroll", syncToolbar, { passive: true });
  syncToolbar();
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== ui.search && !ui.sourceDialog.open && !$("confirmDialog").open
      && !document.activeElement?.closest?.("input, textarea, select")) {
      event.preventDefault();
      ui.search.focus();
    }
    if (event.key === "Escape" && !ui.sheet.hidden) closePrint();
  });

  if (state.document) ui.source.value = state.document;
  saveBasket();
  load();
})();
