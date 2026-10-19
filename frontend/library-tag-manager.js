/* The user's tag directory lives in a page, separately from a question's tag picker. */
((root) => {
  "use strict";
  const kinds = { knowledge: "知识点", method: "解题方法", custom: "自定义", legacy: "历史标签" };
  const historyNames = { assign: "人工贴标", replace: "校正题目标签", "suggestion:accept": "采纳 AI 建议", "suggestion:reject": "拒绝 AI 建议",
    "catalogue:create_tag": "新建标签", "catalogue:update_tag": "修改标签", "catalogue:favorite_tag": "收藏调整",
    "catalogue:activate_tag": "启用标签", "catalogue:deactivate_tag": "停用标签", "catalogue:merge_tags": "合并标签",
    "catalogue:merge_tags_batch": "批量合并标签", "catalogue:delete_tag": "删除标签", "catalogue:delete_tags": "批量删除标签",
    "catalogue:reset_empty": "清空标签目录", "catalogue:apply_template": "追加模板",
    "catalogue:set_parents": "设置标签父级", "catalogue:add_parent": "增加标签父级",
    "catalogue:remove_parent": "移除标签父级", "catalogue:move_parent": "移动标签父级",
    "catalogue:set_related_methods": "调整相关方法" };
  function element(name, className, text) {
    const value = document.createElement(name);
    if (className) value.className = className;
    if (text !== undefined) value.textContent = text;
    return value;
  }
  function button(text, callback, className = "button button-small") {
    const value = element("button", className, text); value.type = "button";
    if (callback) value.addEventListener("click", callback);
    return value;
  }
  function normalized(text) { return String(text || "").normalize("NFKC").toLocaleLowerCase(); }
  function compatible(source, target) { return source.kind === target.kind || (source.kind === "legacy" && ["knowledge", "custom"].includes(target.kind)); }

  function create(options) {
    const mount = options.mount;
    if (!mount) throw new Error("标签管理缺少页面容器");
    // 1.25.47：记住上次看的是「全部标签 / 用过的 / 收藏」，下次点进来还是那一个。
    // 默认仍然是全部标签 —— 不能替人把模板藏起来。
    const SCOPE_KEY = "qb-tag-manager-scope";
    function savedScope() {
      try { const value = root.localStorage?.getItem(SCOPE_KEY); return ["all", "used", "favorite"].includes(value) ? value : "all"; } catch { return "all"; }
    }
    function rememberScope() { try { root.localStorage?.setItem(SCOPE_KEY, state.scope); } catch { /* 存不下就每次从全部开始 */ } }
    const state = { open: false, busy: false, scope: "all", query: "", selected: new Set(), focused: "", anchor: "", hierarchyKind: "", hierarchyNode: "",
      // 1.25.38：进子级时记下「从哪里进来的」，返回上级按原路退；同一个标签有多个
      // 父级时也不至于退不动。
      hierarchyTrail: [],
      // 1.25.49：左侧文件夹树里哪些层是展开的。
      treeOpen: new Set(),
      relations: new Map(), relationLoads: new Map(),
      catalogue: { tags: [] }, shown: [], history: [], notice: "", error: false, scroll: 0, original: null, originalDisplay: "", originalHidden: false,
      view: "list", mapKind: "knowledge", questionContext: null };
    const page = element("section", "tag-directory-workspace"); page.id = "tagManagerWorkspace"; page.hidden = true; page.tabIndex = -1;
    page.setAttribute("aria-labelledby", "tagManagerHeading");
    const header = element("header", "tag-directory-header"), headerText = element("div"), heading = element("h1", "", "标签管理");
    heading.id = "tagManagerHeading"; const total = element("span", "tag-directory-total"); total.id = "tagManagerTotalCount";
    headerText.append(heading, total);
    const back = button("← 返回题库", close, "button button-quiet"); back.id = "tagManagerBack"; header.append(headerText, back);
    const toolbar = element("div", "tag-directory-toolbar"), searchBox = element("label", "tag-directory-search"), searchLabel = element("span", "sr-only", "搜索标签");
    const search = element("input"); search.id = "tagManagerSearch"; search.type = "search"; search.placeholder = "搜索标签、别名或说明"; search.setAttribute("aria-label", "搜索标签、别名或说明");
    searchBox.append(searchLabel, search);
    const newTag = button("＋ 新建标签", () => options.editEntity("tag", null, state.view === "map" ? { kind: state.mapKind } : {
      ...(state.hierarchyKind ? { kind: state.hierarchyKind } : {}), ...(state.hierarchyNode ? { parent_ids: [state.hierarchyNode] } : {}) }), "button button-primary"); newTag.id = "tagManagerCreateTag";
    const templates = button("追加模板", () => options.openTemplates()); templates.id = "tagManagerTemplates";
    const advanced = element("details", "tag-directory-advanced"), advancedTitle = element("summary", "button button-small", "更多"), advancedMenu = element("div", "tag-directory-menu");
    const migrationReport = element("a", "button button-quiet button-small", "导出旧标签映射");
    migrationReport.id = "tagManagerMigrationReport"; migrationReport.href = "/api/library/tags/migration-report";
    migrationReport.setAttribute("download", "");
    const reset = button("清空标签目录", () => run(() => options.clearCatalogue(), "目录操作已完成"), "button button-quiet button-danger"); reset.id = "tagManagerReset";
    advancedMenu.append(migrationReport, reset, element("small", "", "清空全库标签与题目关联，提交前会显示影响范围。")); advanced.append(advancedTitle, advancedMenu);
    const views = element("div", "tag-directory-view-switch"); views.setAttribute("role", "group"); views.setAttribute("aria-label", "标签管理视图");
    const listView = button("列表", () => switchView("list"), "tag-directory-view-button"), mapView = button("标签地图", () => switchView("map"), "tag-directory-view-button");
    listView.id = "tagManagerListView"; mapView.id = "tagManagerMapView"; views.append(listView, mapView);
    const mapType = element("select", "tag-directory-map-kind"); mapType.id = "tagManagerMapKind"; mapType.setAttribute("aria-label", "地图类型");
    for (const kind of ["knowledge", "method", "custom", "legacy"]) mapType.add(new Option(kinds[kind], kind));
    mapType.addEventListener("change", () => { state.mapKind = mapType.value; state.focused = ""; state.selected.clear(); render(); });
    toolbar.append(views, searchBox, mapType, newTag, templates, advanced);
    const status = element("p", "tag-directory-status"); status.id = "tagManagerStatus"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const body = element("div", "tag-directory-body"), sidebar = element("aside", "tag-directory-groups");
    const scopeNav = element("nav", "tag-directory-group-list");
    scopeNav.id = "tagManagerScopes"; scopeNav.setAttribute("aria-label", "标签范围");
    const hierarchyNav = element("nav", "tag-directory-hierarchy-nav");
    hierarchyNav.id = "tagManagerHierarchyNav"; hierarchyNav.setAttribute("aria-label", "按类型浏览标签层级");
    const treeNav = element("nav", "tag-directory-tree-nav");
    treeNav.id = "tagManagerTreeNav"; treeNav.setAttribute("aria-label", "顶层标签");
    const hierarchyHint = element("p", "tag-directory-nav-hint", "点 ▸ 展开文件夹，点名字打开这一层；每一级都能贴题。");
    sidebar.append(hierarchyNav, treeNav, hierarchyHint, scopeNav);
    const results = element("section", "tag-directory-results"), listHeading = element("div", "tag-directory-list-heading"), selectAll = element("input"); selectAll.type = "checkbox";
    selectAll.id = "tagManagerSelectAll"; selectAll.setAttribute("aria-label", "选择当前显示的全部标签");
    const selectAllLabel = element("label", "tag-directory-select-all"); selectAllLabel.append(selectAll, element("span", "", "全选当前结果"));
    const count = element("span", "tag-directory-visible-count"); count.id = "tagManagerVisibleCount"; listHeading.append(selectAllLabel, count);
    const bulk = element("div", "tag-directory-bulk"); bulk.id = "tagManagerBulk"; bulk.hidden = true;
    const selectedCount = element("strong"), hiddenSelection = element("small", "tag-directory-hidden-selection");
    const merge = button("合并标签", openMergeDialog), remove = button("删除标签", deleteSelected, "button button-small button-danger");
    merge.id = "tagManagerBulkMerge"; remove.id = "tagManagerBulkDelete";
    const clearSelection = button("取消选择", () => { state.selected.clear(); state.focused = ""; render(); }, "button button-quiet button-small");
    bulk.append(selectedCount, hiddenSelection, merge, remove, clearSelection);
    const location = element("div", "tag-directory-location"); location.id = "tagManagerLocation";
    // 1.25.12：走进标签管理的人看到的是 299 行「0 题」，而页面从头到尾没说过题库里
    // 有多少道题、其中几道贴过标签。这条把现状摆出来，并给出唯一真正走得通的下一步
    // ——后台已经攒着可以逐条采纳的 AI 建议，采纳不花钱。
    const usageNoteBox = element("div", "tag-directory-usage-note"); usageNoteBox.id = "tagManagerUsageNote";
    const usageNoteText = element("p");
    // 走这条出路要先把标签管理收起来，再让题库筛出待核对的那批 —— 两件事一起做，
    // 人回来时已经在要干的地方了，而不是回到列表首页再自己找一遍。
    const goReview = button("回题库核对 AI 标签建议", () => { close(); options.onReviewSuggestions?.(); }, "button button-small");
    goReview.id = "tagManagerGoReviewSuggestions";
    usageNoteBox.append(usageNoteText, goReview);
    const list = element("div", "tag-directory-list"); list.id = "tagManagerList"; list.setAttribute("aria-label", "标签列表");
    const mapHost = element("div", "tag-directory-map-host"); mapHost.id = "tagManagerMap"; mapHost.hidden = true;
    results.append(location, listHeading, usageNoteBox, bulk, list, mapHost);
    const detail = element("aside", "tag-directory-detail"); detail.id = "tagManagerDetail"; detail.setAttribute("aria-label", "选中标签的信息");
    body.append(sidebar, results, detail);
    const history = element("details", "tag-directory-history"), historySummary = element("summary", "", "操作记录与撤销"), historyList = element("div", "tag-directory-history-list");
    historyList.id = "tagManagerHistoryList"; history.append(historySummary, historyList);
    page.append(header, toolbar, status, body, history); mount.append(page);
    let map = null;
    function ensureMap() {
      if (map || !root.QBLibraryTagMap) return map;
      map = root.QBLibraryTagMap.create({ host: mapHost,
        onQueryChange: (value) => { state.query = value; search.value = value; },
        onSelect: (tag) => { if (state.busy) return; state.focused = String(tag.id); state.selected = new Set([String(tag.id)]); renderDetail(); void ensureRelations(tag); },
        onFindQuestions: (tag) => { if (state.busy) return; close(); options.browseTag(tag); },
        onEdit: (tag) => options.editEntity("tag", tag),
        onAddChild: (tag) => options.editEntity("tag", null, { kind: tag.kind, parent_ids: [tag.id] }),
        onEditParents: (tag) => openRelationDialog(tag, "parents"),
        onRelatedMethods: (tag) => openRelationDialog(tag, "methods") });
      return map;
    }
    function switchView(view, tag = null) {
      if (state.busy) return;
      if (view === "map" && !ensureMap()) { setNotice("标签地图暂时未载入，请刷新后重试。", true); return; }
      state.view = view;
      if (tag) { state.focused = String(tag.id); state.selected = new Set([String(tag.id)]); }
      if (view === "map") {
        const selected = tag || tagById(state.focused); if (selected) state.mapKind = selected.kind;
        if (state.selected.size > 1) state.selected = new Set(state.focused ? [state.focused] : []);
      }
      render();
      if (view === "map") {
        if (tag) map.focus(String(tag.id), { local: true });
        else requestAnimationFrame(() => { if (state.open && state.view === "map") map.fit(); });
      }
    }
    function inHierarchy() { return Boolean(state.hierarchyKind) && state.scope === "all"; }
    function renderView() {
      const mapped = state.view === "map";
      page.classList.toggle("tag-directory-map-view", mapped); sidebar.hidden = mapped;
      location.hidden = mapped || !inHierarchy(); listHeading.hidden = list.hidden = mapped;
      bulk.hidden = mapped || !state.selected.size; mapHost.hidden = !mapped; mapType.hidden = !mapped; mapType.value = state.mapKind;
      listView.setAttribute("aria-pressed", String(!mapped)); mapView.setAttribute("aria-pressed", String(mapped));
      if (mapped) ensureMap()?.update({ catalogue: state.catalogue, kind: state.mapKind, focusId: state.focused, query: state.query, busy: state.busy });
    }

    function selectedTags() { return (state.catalogue.tags || []).filter((tag) => state.selected.has(String(tag.id))); }
    function tagById(id) { return (state.catalogue.tags || []).find((tag) => String(tag.id) === String(id)); }
    function relationIds(tag, field) { return (tag[field] || []).map((value) => String(typeof value === "object" ? value.id : value)); }
    function parentsOf(tag) { return relationIds(tag, "parent_ids").map(tagById).filter(Boolean); }
    function childrenOf(tag) {
      const ids = new Set(relationIds(tag, "child_ids"));
      (state.catalogue.tags || []).forEach((value) => { if (relationIds(value, "parent_ids").includes(String(tag.id))) ids.add(String(value.id)); });
      return [...ids].map(tagById).filter(Boolean).sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN"));
    }
    function descendantsOf(tag) {
      const found = new Set(), pending = childrenOf(tag).map((value) => String(value.id));
      while (pending.length) { const id = pending.pop(); if (found.has(id) || id === String(tag.id)) continue;
        found.add(id); const child = tagById(id); if (child) childrenOf(child).forEach((value) => pending.push(String(value.id))); }
      return found;
    }
    function parentPaths(tag, seen = new Set()) {
      if (!tag || seen.has(String(tag.id))) return [];
      const next = new Set(seen); next.add(String(tag.id)); const parents = parentsOf(tag);
      if (!parents.length) return [[tag]];
      return parents.flatMap((parent) => parentPaths(parent, next).map((path) => [...path, tag])).slice(0, 40);
    }
    function directCount(tag) { return tag.direct_count ?? tag.count ?? 0; }
    function subtreeCount(tag) { return tag.subtree_count ?? directCount(tag); }
    function browseHierarchy(tag) {
      if (state.busy || !tag.kind) return;
      if (state.view === "map") { state.focused = String(tag.id); state.selected = new Set([String(tag.id)]); state.mapKind = tag.kind; state.query = ""; search.value = ""; render(); map?.focus(String(tag.id), { local: true }); return; }
      if (String(state.hierarchyNode) !== String(tag.id)) state.hierarchyTrail.push(String(state.hierarchyNode || ""));
      state.hierarchyKind = tag.kind; state.hierarchyNode = String(tag.id); state.scope = "all"; state.query = ""; search.value = "";
      // 1.25.49：进入某一层时，把它上面的文件夹展开，保证这一层在左树里看得见。
      parentPaths(tag).forEach((path) => path.forEach((node) => {
        if (String(node.id) !== String(tag.id)) state.treeOpen.add(String(node.id));
      }));
      render(); list.scrollTop = 0; location.querySelector("button")?.focus({ preventScroll: true });
    }
    function goUp() {
      if (!state.hierarchyNode) return;
      state.hierarchyNode = state.hierarchyTrail.length ? state.hierarchyTrail.pop() : "";
      render();
    }
    async function ensureRelations(tag) {
      if (!options.request || !tag.kind) return;
      const id = String(tag.id), revision = state.catalogue.revision, key = revision + ":" + id;
      if (state.relations.has(key) || state.relationLoads.has(key)) return;
      const loading = options.request("/api/library/tags/" + encodeURIComponent(id) + "/relations"); state.relationLoads.set(key, loading);
      try {
        const result = await loading; state.relations.set(key, result);
        if (state.catalogue.revision !== revision || !result.tag || (result.revision !== undefined && result.revision !== revision)) return;
        const current = tagById(id); if (!current) return;
        for (const field of ["parent_ids", "child_ids", "related_method_ids", "direct_count", "subtree_count"]) {
          if (result.tag[field] !== undefined) current[field] = result.tag[field];
        }
        if (String(state.focused) === id && !state.busy) renderDetail();
      } catch (_) { /* The loaded catalogue remains usable if relation refresh is unavailable. */ }
      finally { state.relationLoads.delete(key); }
    }
    function setNotice(text, error = false) { state.notice = text; state.error = error; status.textContent = text; status.classList.toggle("error", error); }
    function setBusy(value) {
      state.busy = value; page.setAttribute("aria-busy", String(value));
      page.querySelectorAll("button, input, select, summary").forEach((control) => {
        if (value) { control.dataset.busyWasDisabled = control.disabled ? "1" : "0"; control.disabled = true; }
        else if (control.dataset.busyWasDisabled !== undefined) { control.disabled = control.dataset.busyWasDisabled === "1"; delete control.dataset.busyWasDisabled; }
      });
      if (state.view === "map") map?.update({ busy: value });
    }
    async function reloadHistory() {
      try { const data = await options.request("/api/library/tags/operations"); renderHistory(data.operations || []); }
      catch (error) { historyList.replaceChildren(element("p", "", "记录暂时无法载入：" + error.message)); }
    }
    async function run(operation, message = "已保存，可在操作记录中撤销") {
      if (state.busy) return false;
      setBusy(true); setNotice("正在保存…");
      try {
        const result = await operation();
        if (result === false || result === null) { setNotice(""); return false; }
        const catalogue = result?.catalogue || await options.loadCatalogue();
        render(catalogue); setNotice(message); await reloadHistory(); return true;
      } catch (error) { setNotice(error.message + "；选择仍保留，请检查后重试。", true); return false; }
      finally { setBusy(false); if (state.questionContext) renderDetail(); }
    }
    async function show(context = {}) {
      if (state.open) { if (context.view === "map") switchView("map", tagById(context.tagId)); else search.focus(); return; }
      state.scroll = window.scrollY; state.original = mount.querySelector(".library-workspace");
      if (state.original) { state.originalDisplay = state.original.style.display; state.originalHidden = state.original.hidden; state.original.hidden = true; state.original.style.display = "none"; }
      state.open = true; page.hidden = false; document.body.classList.add("library-tag-manager-open"); window.scrollTo(0, 0);
      // 1.25.47：恢复上次的「全部 / 用过的 / 收藏」；查题上下文进来时不恢复。
      // 1.25.50：第一次进来直接打开「知识点」文件夹 —— 标签本来就该先按文件夹看。
      if (!context.questionContext) {
        let stored = null;
        try { stored = root.localStorage?.getItem(SCOPE_KEY); } catch { /* 存不下就按第一次算 */ }
        state.scope = savedScope();
        state.hierarchyKind = stored === null ? "knowledge" : "";
        state.hierarchyNode = ""; state.hierarchyTrail = [];
      }
      setBusy(true); setNotice("正在载入标签目录…");
      state.questionContext = context.questionContext || null;
      try { render(await options.loadCatalogue()); setNotice(""); await reloadHistory(); }
      catch (error) { setNotice("目录暂时无法载入：" + error.message, true); }
      finally { setBusy(false); if (state.open) {
        if (context.view === "map") { state.query = ""; search.value = ""; switchView("map", tagById(context.tagId)); }
        search.focus({ preventScroll: true });
      } }
    }
    function close() {
      if (!state.open || state.busy) return;
      state.open = false; page.hidden = true; document.body.classList.remove("library-tag-manager-open");
      if (state.original) { state.original.hidden = state.originalHidden; state.original.style.display = state.originalDisplay; }
      window.scrollTo(0, state.scroll); state.questionContext = null; options.onClose?.();
    }
    // 1.25.38：点名字只负责「看」。以前这一下会清空勾选、把它设成唯一选中项，批量条
    // 立刻出现「已选 1 个 · 合并标签 · 删除标签」—— 读一眼说明就进了待删除状态。
    // 现在：点名字 = 打开详情；要批量处理，用前面的勾选框（带 Shift / Ctrl 点名字仍可加选）。
    function choose(tag, event = {}) {
      const id = String(tag.id);
      if (event.shiftKey && state.anchor) {
        const from = state.shown.findIndex((value) => String(value.id) === state.anchor), to = state.shown.findIndex((value) => String(value.id) === id);
        if (from >= 0 && to >= 0) state.shown.slice(Math.min(from, to), Math.max(from, to) + 1).forEach((value) => state.selected.add(String(value.id)));
        else state.selected.add(id);
      } else if (event.ctrlKey || event.metaKey) { if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id); state.anchor = id; }
      state.anchor = id;
      state.focused = id; render();
      void ensureRelations(tag);
      if (state.view === "list") page.querySelector('[data-tag-id="' + CSS.escape(id) + '"] .tag-directory-name')?.focus({ preventScroll: true });
    }
    function renderSidebar() {
      const scroll = sidebar.scrollTop, tags = state.catalogue.tags || [];
      hierarchyNav.replaceChildren(); treeNav.replaceChildren(); scopeNav.replaceChildren();
      const entry = (nav, id, label, number, active, onPick, extra) => {
        const control = button("", onPick, "tag-directory-group" + (active ? " active" : ""));
        control.dataset.navId = id; control.setAttribute("aria-pressed", String(active));
        control.append(element("span", "", label), element("small", "", number));
        if (extra) Object.assign(control.dataset, extra);
        nav.append(control);
      };
      for (const kind of ["knowledge", "method", "custom", "legacy"]) {
        // Clicking the level you are already in steps up one notch: first out of
        // the tag being read, then out of the level entirely.
        entry(hierarchyNav, kind, kinds[kind], tags.filter((tag) => tag.kind === kind).length,
          state.hierarchyKind === kind && state.scope === "all",
          () => {
            const inside = state.hierarchyKind === kind && state.scope === "all";
            if (!inside) { state.hierarchyKind = kind; state.hierarchyNode = ""; state.hierarchyTrail = []; }
            else if (state.hierarchyNode) { state.hierarchyNode = ""; state.hierarchyTrail = []; }
            else state.hierarchyKind = "";
            state.scope = "all"; rememberScope();
            render();
          });
      }
      // 1.25.49：左侧是真正的文件夹树 —— 展开哪些层由 state.treeOpen 决定，
      // 不再是「只显示当前所在的那一条路径」。
      if (state.hierarchyKind && state.scope === "all") {
        // 1.25.49：按文件夹来做 —— 每层有自己的 ▸/▾，展开状态记在 state.treeOpen 里；
        // 点箭头只展开/收起这一层，点名字进入这一层（中间显示文件夹内容）。
        const kindTags = tags.filter((tag) => tag.kind === state.hierarchyKind);
        const roots = kindTags.filter((tag) => !parentsOf(tag).some((parent) => parent.kind === tag.kind))
          .sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN"));
        treeNav.append(element("h2", "tag-directory-nav-heading", "文件夹"));
        const shown = new Set();
        const branch = (tag, depth, ancestors) => {
          const id = String(tag.id);
          if (shown.has(id)) return;         // 一个标签挂多个上级时只画一次
          shown.add(id);
          const children = childrenOf(tag).filter((child) => child.kind === state.hierarchyKind && !ancestors.has(String(child.id)));
          const open = state.treeOpen.has(id);
          const row = element("div", "tag-directory-tree-row");
          row.dataset.treeTagId = id;
          row.dataset.depth = String(depth);
          row.style.paddingLeft = (9 + depth * 16) + "px";
          const toggle = children.length
            ? button(open ? "▾" : "▸", () => { if (open) state.treeOpen.delete(id); else state.treeOpen.add(id); render(); }, "tag-directory-tree-toggle")
            : element("span", "tag-directory-tree-toggle is-empty");
          toggle.setAttribute("aria-expanded", String(open));
          const name = button(tag.name, () => browseHierarchy(tag), "tag-directory-tree-name"
            + (String(tag.id) === String(state.hierarchyNode) ? " current" : ""));
          name.title = "打开「" + tag.name + "」这一层";
          row.append(toggle, name, element("small", "", directCount(tag) + " 题"));
          treeNav.append(row);
          if (open) {
            const next = new Set([...ancestors, id]);
            children.forEach((child) => branch(child, depth + 1, next));
          }
        };
        roots.forEach((tag) => branch(tag, 0, new Set()));
        if (!roots.length) treeNav.append(element("p", "tag-directory-nav-hint", "这一类还没有标签。"));
      }
      scopeNav.append(element("h2", "tag-directory-nav-heading", "快速查看"));
      const flat = (target) => () => { state.scope = target; state.hierarchyKind = ""; state.hierarchyNode = ""; state.hierarchyTrail = []; rememberScope(); render(); };
      entry(scopeNav, "all", "全部标签", tags.length, state.scope === "all", flat("all"));
      // 1.25.38：目录里大多是 0 题，先把真正用过的挑出来，比在 352 行里翻有用。
      entry(scopeNav, "used", "用过的", tags.filter((tag) => Number(tag.subtree_count) > 0 || Number(tag.count) > 0).length, state.scope === "used", flat("used"));
      entry(scopeNav, "favorite", "收藏", tags.filter((tag) => tag.favorite).length, state.scope === "favorite", flat("favorite"));
      sidebar.scrollTop = scroll;
    }
    function renderLocation() {
      location.replaceChildren(); location.hidden = !inHierarchy();
      if (!inHierarchy()) return;
      const current = tagById(state.hierarchyNode), home = button(kinds[state.hierarchyKind] + " · 顶级", () => { state.hierarchyNode = ""; state.hierarchyTrail = []; render(); }, "tag-directory-path-button");
      location.append(home);
      if (current) {
        // 1.25.38：原来只有一条小路径能回去，退出子级得靠「再点一次同一个分类」这种
        // 没人知道的动作。给一颗明确的「返回上级」，按进来的路退。
        const up = button("← 返回上级", goUp, "button button-quiet button-small tag-directory-up");
        up.id = "tagManagerUp";
        location.append(up);
        const paths = parentPaths(current), pathList = element("div", "tag-directory-paths");
        paths.forEach((path) => {
          const row = element("div", "tag-directory-path");
          path.forEach((tag, index) => { if (index) row.append(element("span", "tag-directory-path-separator", "›"));
            const control = button(tag.name, () => browseHierarchy(tag), "tag-directory-path-button" + (String(tag.id) === String(current.id) ? " current" : ""));
            control.dataset.pathTagId = tag.id; row.append(control); }); pathList.append(row);
        });
        location.append(pathList, element("small", "", state.query ? "搜索全部类型的标签" : "正在查看「" + current.name + "」的直接子标签"));
        location.append(button("选择当前标签", () => choose(current), "button button-small"),
          button("＋ 添加子标签", () => options.editEntity("tag", null, { parent_ids: [current.id], kind: current.kind }), "button button-small"));
      } else location.append(element("small", "", state.query ? "搜索全部类型的标签" : "展开箭头查看子标签；每一级都可以贴题。"));
    }
    // 1.25.12：把「现在是什么情况、下一步做什么」写成一句话放在列表正上方。以前这
    // 一块只有一排「0 题」，看的人无从判断是标签统计坏了还是题真的一道都没贴，更
    // 不知道后台已经攒着几百条能直接采纳的 AI 建议。
    //
    // 句子和按钮文案放在一起算，别拆成两处：数字变了只改这里就够，句子不会因此跟
    // 按钮对不上（说一句 443 条、按钮却不带数字，是最容易出的那种不一致）。
    function usageNote(catalogue) {
      const data = catalogue || {};
      const published = Number(data.published_questions) || 0;
      const tagged = Number(data.tagged_questions) || 0;
      const pending = Number(data.questions_pending_review) || 0;
      const actionable = Number(data.questions_pending_actionable) || 0;
      // 标签都贴完了，或者题库里还没有题，这句话没有内容可说 —— 别摆一条空框。
      if (!published || published - tagged <= 0) return null;
      const lead = `题库 ${published} 道题里只有 ${tagged} 道贴了标签，所以下面几乎都是「0 题」。`;
      if (pending) {
        // 1.25.13：这里数的是「有多少道题等你核对」，和题库页那个「AI 建议待核对」筛
        // 选是同一个数、同一套算法。上一版这里数的是建议条数（一道题可能挂两条），
        // 两个数字摆在两个页面上，人来回一看只会以为有一个算错了。
        const unreachable = pending - actionable;
        return {
          text: lead + `另有 ${pending} 道题有 AI 标签建议等你核对，采纳后直接贴上，不用花钱。`,
          button: `回题库核对这 ${pending} 道题的 AI 标签建议`,
          title: "回到题库，按「AI 建议待核对」筛选，逐题看过再采纳。"
            + "建议是 AI 已经拟好的，采纳这一步不再调用 AI，也不消耗额度。"
            + (unreachable > 0
              ? `其中 ${actionable} 道现在就能采纳，另外 ${unreachable} 道对不上现在的标签库，需要重新生成。`
              : `这 ${pending} 道题现在都能采纳。`)
        };
      }
      return {
        text: lead + "在题库里选中一道题，就能在右侧「标签」里给它贴标签。",
        button: "回题库给题目贴标签",
        title: "回到题库，选中一道题，在右侧「标签」里挑标签贴上去。"
      };
    }
    function renderUsageNote() {
      const note = usageNote(state.catalogue);
      usageNoteBox.hidden = !note;
      if (!note) return;
      usageNoteText.textContent = note.text;
      goReview.textContent = note.button;
      goReview.title = note.title;
    }
    function renderList() {
      const scroll = list.scrollTop, query = normalized(state.query.trim());
      state.shown = (state.catalogue.tags || []).filter((tag) => {
        // 1.25.38：搜索不再被「当前分类」挡住 —— 原来在知识点里搜不到解题方法，
        // 可搜索框并没有说范围。现在有搜索词就搜全部类型，结果里标出类型和上级路径。
        if (!query && state.hierarchyKind && state.scope === "all") {
          if (tag.kind !== state.hierarchyKind) return false;
          if (state.hierarchyNode && !relationIds(tag, "parent_ids").includes(String(state.hierarchyNode))) return false;
          if (!state.hierarchyNode && parentsOf(tag).some((parent) => parent.kind === tag.kind)) return false;
        }
        // 1.25.38：「用过的」只看确实贴过题的标签（含下级）。
        if (state.scope === "used" && !(Number(tag.subtree_count) > 0 || Number(tag.count) > 0)) return false;
        if (state.scope === "favorite" && !tag.favorite) return false;
        const searchText = normalized([tag.name, tag.description, tag.conditions, ...(tag.aliases || []),
          ...parentPaths(tag).map((path) => path.map((node) => node.name).join(" "))].join(" "));
        return !query || query.split(/\s+/).every((part) => searchText.includes(part));
      }).sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN", { numeric: true }));
      list.replaceChildren(); count.textContent = state.shown.length + " 个标签";
      renderUsageNote();
      selectAll.checked = state.shown.length > 0 && state.shown.every((tag) => state.selected.has(String(tag.id)));
      selectAll.indeterminate = !selectAll.checked && state.shown.some((tag) => state.selected.has(String(tag.id))); selectAll.disabled = !state.shown.length;
      if (!state.shown.length) {
        const empty = element("div", "tag-directory-empty"); empty.append(element("strong", "", state.query ? "没有找到这个标签" : "这里还没有标签"),
          element("p", "", state.query ? "换个名称、别名或说明搜索。" : state.hierarchyNode ? "这个标签已经可以贴题；需要细化时可添加子标签。" : "可以新建自己的标签，也可以追加需要的模板。")); list.append(empty);
      }
      const rowFor = (tag) => {
        const id = String(tag.id), row = element("div", "tag-directory-row" + (state.selected.has(id) ? " selected" : "") + (String(state.focused) === id ? " focused" : "") + (tag.active === false ? " inactive" : "")); row.dataset.tagId = id;
        const check = element("input"); check.type = "checkbox"; check.checked = state.selected.has(id); check.setAttribute("aria-label", "选择标签「" + tag.name + "」");
        check.addEventListener("change", () => { if (check.checked) { state.selected.add(id); state.focused = id; } else { state.selected.delete(id); if (state.focused === id) state.focused = ""; } state.anchor = id; render();
          page.querySelector('[data-tag-id="' + CSS.escape(id) + '"] > input')?.focus({ preventScroll: true }); });
        const dot = element("span", "tag-directory-color"); dot.setAttribute("aria-hidden", "true"); if (/^#[0-9a-f]{6}$/i.test(tag.color || "")) dot.style.backgroundColor = tag.color;
        const name = button(tag.name, (event) => choose(tag, event), "tag-directory-name"); name.title = tag.description || tag.name;
        // 1.25.38：双击名字直接进它的子标签，和文件管理器一样。
        name.addEventListener("dblclick", (event) => { if (childrenOf(tag).length) { event.preventDefault(); browseHierarchy(tag); } });
        const nameCell = element("div", "tag-directory-name-cell"); nameCell.append(name);
        // 搜索时补一行上级路径：结果只写「知识点」，看不出它到底属于哪一章。
        if (query) {
          const paths = parentPaths(tag).map((path) => path.map((node) => node.name).join(" › ")).filter(Boolean);
          if (paths.length) nameCell.append(element("small", "tag-directory-path-line", paths[0] + (paths.length > 1 ? "　等 " + paths.length + " 条路径" : "")));
        }
        const type = element("small", "tag-directory-kind", (tag.favorite ? "★ " : "") + (kinds[tag.kind] || "标签") + (tag.active === false ? " · 停用" : ""));
        const usage = button(directCount(tag) + (childrenOf(tag).length ? " / " + subtreeCount(tag) : "") + " 题", () => { if (state.busy) return; close(); options.browseTag(tag); }, "tag-directory-usage");
        usage.title = "本级 " + directCount(tag) + " 题，包含下级 " + subtreeCount(tag) + " 题；点击查看含下级的题目";
        // 1.25.38：一整列「0 题」太抢眼。没用到的标签把数字压淡，有题的才醒目。
        if (!directCount(tag) && !subtreeCount(tag)) usage.classList.add("is-zero");
        const branch = childrenOf(tag).length && ["knowledge", "method"].includes(tag.kind) ? button("›", () => browseHierarchy(tag), "tag-directory-expand") : element("span", "tag-directory-expand-placeholder");
        if (branch.tagName === "BUTTON") { branch.title = "查看「" + tag.name + "」的子标签"; branch.setAttribute("aria-label", branch.title); branch.dataset.expandTagId = id; }
        row.append(check, dot, nameCell, type, usage, branch);
        return row;
      };
      // 1.25.46：300 个标签原来按名称混排，知识点、方法、历史标签一行一个颜色。
      // 现在按类型分段，段头写数量和名字；段内仍按名称排。行里的「类型」只在
      // 只有一种类型时才有用，分段后收掉。
      const kindCount = new Set(state.shown.map((tag) => tag.kind)).size;
      if (kindCount > 1) {
        list.classList.add("grouped");
        // 段内先摆顶级标签（章节），再摆下一层 —— 「初中·函数」不该夹在一堆
        // 叶子标签中间靠拼音排。
        const treeOrder = (a, b) => {
          const rootA = parentsOf(a).some((parent) => parent.kind === a.kind) ? 1 : 0;
          const rootB = parentsOf(b).some((parent) => parent.kind === b.kind) ? 1 : 0;
          return rootA - rootB || String(a.name).localeCompare(String(b.name), "zh-CN", { numeric: true });
        };
        for (const [kind, label] of [["knowledge", "知识点"], ["method", "解题方法"], ["custom", "自定义标签"], ["legacy", "历史标签"]]) {
          const tags = state.shown.filter((tag) => tag.kind === kind).sort(treeOrder);
          if (!tags.length) continue;
          const heading = element("div", "tag-directory-section");
          heading.append(element("span", "", label), element("small", "", tags.length + " 个"));
          list.append(heading);
          tags.forEach((tag) => list.append(rowFor(tag)));
        }
      } else {
        list.classList.remove("grouped");
        state.shown.forEach((tag) => list.append(rowFor(tag)));
      }
      list.scrollTop = scroll;
      bulk.hidden = !state.selected.size; selectedCount.textContent = "已选 " + state.selected.size + " 个";
      const hiddenCount = state.selected.size - state.shown.filter((tag) => state.selected.has(String(tag.id))).length;
      hiddenSelection.textContent = hiddenCount ? "其中 " + hiddenCount + " 个不在当前结果中" : "";
      merge.disabled = selectedTags().length < 1;
    }
    function info(title, text) { const row = element("div", "tag-directory-info"); row.append(element("span", "", title), element("p", "", text || "—")); return row; }
    function renderRelations(tag) {
      const block = element("section", "tag-directory-relations"); block.id = "tagManagerRelations";
      const heading = element("div", "tag-directory-relation-heading"); heading.append(element("h3", "", (tag.kind === "method" ? "方法" : tag.kind === "legacy" ? "历史标签" : tag.kind === "custom" ? "自定义标签" : "知识") + "上下级"),
        button("查看层级", () => browseHierarchy(tag), "button button-quiet button-small"));
      block.append(heading, element("p", "tag-directory-hint", "当前标签和上下级都可以贴题；增加子标签不会改变已经贴好的题目。"));
      const parentHeading = element("div", "tag-directory-relation-heading"); parentHeading.append(element("h4", "", "父标签"),
        button("设置父级", () => openRelationDialog(tag, "parents"), "button button-quiet button-small"));
      block.append(parentHeading);
      const parents = parentsOf(tag);
      if (!parents.length) block.append(element("p", "tag-directory-hint", "顶级标签，暂时没有父级。"));
      parents.forEach((parent) => {
        const row = element("div", "tag-directory-relation-row"); row.dataset.parentId = parent.id;
        const name = button(parent.name, () => { choose(parent); browseHierarchy(parent); }, "tag-directory-relation-name");
        name.title = "查看父标签「" + parent.name + "」";
        row.append(name, button("移到…", () => openRelationDialog(tag, "move", parent), "button button-quiet button-small"),
          button("移除", () => run(() => options.action("remove_parent", { tag_id: tag.id, parent_id: parent.id }), "已移除这条父级关系；标签和题目关联保留"), "button button-quiet button-small"));
        block.append(row);
      });
      const addParent = button("＋ 再增加父级", () => openRelationDialog(tag, "add"), "button button-small"); addParent.id = "tagManagerAddParent"; block.append(addParent);
      const childHeading = element("div", "tag-directory-relation-heading"); childHeading.append(element("h4", "", "子标签"));
      const addChild = button("＋ 添加子标签", () => options.editEntity("tag", null, { parent_ids: [tag.id], kind: tag.kind }),
        "button button-small");
      addChild.id = "tagManagerCreateChild"; childHeading.append(addChild); block.append(childHeading);
      const children = childrenOf(tag), childList = element("div", "tag-directory-relation-children");
      children.forEach((child) => { const control = button(child.name, () => { choose(child); browseHierarchy(child); }, "tag-directory-group-chip"); control.dataset.childId = child.id; childList.append(control); });
      if (!children.length) childList.append(element("p", "tag-directory-hint", "需要更具体时，可以为它添加子标签。")); block.append(childList);
      if (tag.kind === "knowledge") {
        const methodHeading = element("div", "tag-directory-relation-heading"); methodHeading.append(element("h4", "", "相关方法"),
          button("设置相关方法", () => openRelationDialog(tag, "methods"), "button button-quiet button-small")); block.append(methodHeading);
        const methods = relationIds(tag, "related_method_ids").map(tagById).filter(Boolean), methodList = element("div", "tag-directory-relation-children");
        methods.forEach((method) => { const control = button(method.name, () => { choose(method); browseHierarchy(method); }, "tag-directory-group-chip"); control.dataset.relatedMethodId = method.id; methodList.append(control); });
        if (!methods.length) methodList.append(element("p", "tag-directory-hint", "可关联方法供贴标时参考；关联不会自动给题目贴标，也不参与知识点继承筛选。")); block.append(methodList);
      }
      detail.append(block);
    }
    function openRelationDialog(tag, mode, fromParent = null) {
      if (state.busy) return;
      const multiple = ["parents", "methods"].includes(mode), methodMode = mode === "methods",
        blocked = descendantsOf(tag), existingIds = new Set(relationIds(tag, methodMode ? "related_method_ids" : "parent_ids"));
      const candidates = (state.catalogue.tags || []).filter((candidate) => {
        if (candidate.kind !== (methodMode ? "method" : tag.kind)) return false;
        if (candidate.active === false && !existingIds.has(String(candidate.id))) return false;
        if (!methodMode && (String(candidate.id) === String(tag.id) || blocked.has(String(candidate.id)))) return false;
        if (!multiple && existingIds.has(String(candidate.id))) return false;
        return true;
      }).sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN"));
      const selected = new Set(multiple ? existingIds : []), dialog = element("dialog", "tag-directory-relation-dialog"),
        title = element("h2", "", { parents: "设置父标签", add: "再增加一个父标签", move: "移动这条父级关系", methods: "设置相关方法" }[mode]);
      const caption = element("p", "tag-directory-hint", "正在整理「" + tag.name + "」" + (fromParent ? "：将父级「" + fromParent.name + "」换成下面的一个标签，其他父级保留。" :
        methodMode ? "。相关方法独立于知识上下级，不会自动给题目添加标签。" : "。父标签可以有多个；自己和后代不能作为父级。"));
      const relationSearch = element("input", "tag-directory-relation-search"); relationSearch.type = "search"; relationSearch.placeholder = "搜索名称、别名或说明"; relationSearch.setAttribute("aria-label", "搜索可选择的标签");
      const candidatesList = element("div", "tag-directory-relation-options"), message = element("p", "tag-directory-hint"); message.setAttribute("role", "status");
      const controls = element("footer"), cancel = button("取消", () => dialog.close()), save = button("保存", async () => {
        if (!multiple && selected.size !== 1) { message.textContent = "请选择一个标签。"; return; }
        if (multiple && selected.size === existingIds.size && [...selected].every((id) => existingIds.has(id))) { dialog.close(); return; }
        const ids = [...selected];
        const payload = mode === "parents" ? { tag_id: tag.id, parent_ids: ids } : mode === "methods" ? { tag_id: tag.id, related_method_ids: ids } : mode === "move" ?
          { tag_id: tag.id, from_parent_id: fromParent.id, to_parent_id: ids[0] } : { tag_id: tag.id, parent_id: ids[0] };
        const action = { parents: "set_parents", methods: "set_related_methods", move: "move_parent", add: "add_parent" }[mode];
        save.disabled = cancel.disabled = relationSearch.disabled = true; candidatesList.querySelectorAll("input").forEach((input) => { input.disabled = true; });
        const succeeded = await run(() => options.action(action, payload), "已保存关系，可在操作记录中撤销；原题标签保持有效");
        if (succeeded) dialog.close();
        else { save.disabled = cancel.disabled = relationSearch.disabled = false; candidatesList.querySelectorAll("input").forEach((input) => { input.disabled = false; }); message.textContent = state.notice || "选择已保留。"; }
      }, "button button-primary");
      save.id = "tagManagerRelationSave"; controls.append(cancel, save);
      function renderCandidates() {
        const query = normalized(relationSearch.value.trim()); candidatesList.replaceChildren();
        const shown = candidates.filter((candidate) => !query || normalized([candidate.name, ...(candidate.aliases || []), candidate.description].join(" ")).includes(query));
        shown.forEach((candidate) => {
          const row = element("label", "tag-directory-relation-option"), input = element("input"); input.type = multiple ? "checkbox" : "radio"; input.name = "tag-directory-parent";
          input.checked = selected.has(String(candidate.id)); input.value = candidate.id; input.disabled = state.busy;
          input.addEventListener("change", () => { if (!multiple) selected.clear(); if (input.checked) selected.add(String(candidate.id)); else selected.delete(String(candidate.id));
            message.textContent = "已选 " + selected.size + " 个"; });
          const text = element("span"), path = parentPaths(candidate)[0] || [candidate]; text.append(element("strong", "", candidate.name),
            element("small", "", path.map((node) => node.name).join(" › ") + (candidate.active === false ? " · 已停用" : "")));
          row.append(input, text); candidatesList.append(row);
        });
        if (!shown.length) candidatesList.append(element("p", "tag-directory-hint", candidates.length ? "没有找到匹配标签。" : "暂无可选标签；可以先新建同类型的标签。"));
      }
      relationSearch.addEventListener("input", (event) => { if (!event.isComposing) renderCandidates(); }); relationSearch.addEventListener("compositionend", renderCandidates);
      dialog.append(title, caption, relationSearch, candidatesList, message, controls); document.body.append(dialog);
      dialog.addEventListener("cancel", (event) => { if (state.busy) event.preventDefault(); }); dialog.addEventListener("close", () => dialog.remove());
      renderCandidates(); dialog.showModal(); relationSearch.focus();
    }
    function renderDetail() {
      detail.replaceChildren();
      const selected = selectedTags();
      // 1.25.38：详情跟着「正在看的标签」走；勾选只决定批量操作的范围。
      const focused = state.focused ? tagById(state.focused) : null;
      detail.dataset.empty = focused || selected.length ? "false" : "true";
      if (!focused) {
        if (!selected.length) {
          detail.append(element("h2", "", "标签信息"), element("p", "tag-directory-hint", "点一下标签名字看说明和上下级；勾选框只用于批量整理。"));
          return;
        }
        detail.append(element("h2", "", "已选 " + selected.length + " 个标签"), element("p", "tag-directory-hint", "上方的合并、删除只作用于勾选的标签；点名字可以看某一个的说明。"));
        const parents = new Map();
        selected.forEach((value) => parentsOf(value).forEach((parent) => parents.set(String(parent.id), parent.name)));
        const block = element("div", "tag-directory-shared-groups"); block.append(element("h3", "", "共同的上级标签"));
        if (parents.size) parents.forEach((name) => block.append(info(name, "所选标签挂在它下面")));
        else block.append(element("p", "tag-directory-hint", "所选标签都没有上级标签。"));
        detail.append(block); return;
      }
      const tag = focused;
      const title = element("div", "tag-directory-detail-title"); title.append(element("h2", "", tag.name), element("small", "", kinds[tag.kind] || "标签")); detail.append(title);
      if (state.questionContext) {
        const context = state.questionContext, originalTag = tagById(context.tagId), block = element("section", "tag-directory-question-context");
        block.id = "tagManagerQuestionContext";
        block.append(element("strong", "", context.label || "正在查看题目标签"), element("p", "", "本题当前标签：" + (originalTag?.name || "已移除")));
        if (context.source) block.append(element("small", "", context.source));
        const saved = options.questionSaveState?.(context);
        if (saved?.message) block.append(element("small", "", saved.message));
        if (saved?.attempt && options.retryQuestionSave) {
          const targetId = saved.attempt.payload?.replace?.to_tag_id;
          const retry = button("核实上次标签操作", async () => {
            const confirmed = await run(() => options.retryQuestionSave(context), "上次标签操作已核实，可继续整理");
            if (confirmed && targetId) { context.tagId = String(targetId); render(); }
          }); retry.id = "tagManagerRetryQuestionTag"; retry.disabled = state.busy; block.append(retry);
        }
        if (options.replaceForQuestion) {
          const replace = button(tag.id === context.tagId ? "当前题目正在使用此标签" : "将本题这条标签改为「" + tag.name + "」", async () => {
            const saved = await run(() => options.replaceForQuestion(context, tag), "本题标签已保存；返回题库可继续阅读，可在操作记录中撤销");
            if (saved) { context.tagId = String(tag.id); renderDetail(); }
          }, "button button-primary"); replace.id = "tagManagerReplaceQuestionTag";
          replace.disabled = state.busy || Boolean(saved?.attempt) || tag.id === context.tagId || tag.active === false || !originalTag || tag.kind !== originalTag.kind || (options.contextValid && !options.contextValid(context));
          if (originalTag && tag.kind !== originalTag.kind) block.append(element("small", "", "跨类型的方法请在题目中使用“添加标签”。"));
          block.append(replace);
        }
        detail.append(block);
      }
      const edit = button("编辑标签", () => options.editEntity("tag", tag), "button button-primary"); edit.id = "tagManagerEditTag";
      const usage = button("本级 " + directCount(tag) + " 题 · 含下级 " + subtreeCount(tag) + " 题 →", () => { close(); options.browseTag(tag); }, "tag-directory-detail-usage");
      detail.append(edit, usage, info("说明", tag.description), info("别名", (tag.aliases || []).join("、")));
      renderRelations(tag);
      if ((tag.levels || []).length) detail.append(info("适用学段", tag.levels.join("、")));
      if (tag.conditions) detail.append(info("适用条件", tag.conditions));
      const actions = element("div", "tag-directory-detail-actions");
      actions.append(button(tag.favorite ? "取消收藏" : "收藏", () => run(() => options.action("favorite_tag", { tag_id: tag.id }))),
        button(tag.active === false ? "启用标签" : "停用标签", () => run(() => options.action(tag.active === false ? "activate_tag" : "deactivate_tag", { tag_id: tag.id }))),
        button("删除标签", () => run(() => options.deleteTag(tag)), "button button-danger button-small")); detail.append(actions);
    }
    function render(catalogue) {
      const active = document.activeElement, focusedRow = active?.closest(".tag-directory-row"), focusedId = focusedRow?.dataset.tagId,
        focusedControl = active?.matches("input") ? "> input" : active?.classList.contains("tag-directory-usage") ? ".tag-directory-usage" : ".tag-directory-name";
      if (catalogue) { if (catalogue.revision !== state.catalogue.revision) state.relations.clear(); state.catalogue = catalogue; }
      const existing = new Set((state.catalogue.tags || []).map((tag) => String(tag.id)));
      state.selected.forEach((id) => { if (!existing.has(id)) state.selected.delete(id); });
      if (state.focused && !existing.has(state.focused)) state.focused = "";
      if (state.hierarchyNode && !existing.has(state.hierarchyNode)) state.hierarchyNode = "";
      const busy = state.busy; if (busy) setBusy(false);
      total.textContent = (state.catalogue.tags || []).length + " 个标签";
      // 1.25.38：新建按钮会悄悄沿用当前查看的上级，名字上先说清楚要建在哪儿。
      newTag.textContent = state.hierarchyNode ? "＋ 新建子标签" : "＋ 新建标签";
      newTag.title = state.hierarchyNode
        ? "建在「" + (tagById(state.hierarchyNode)?.name || "当前标签") + "」下面"
        : "建一个顶级标签";
      if (state.view === "list") { renderSidebar(); renderLocation(); renderList(); }
      else state.shown = [];
      renderDetail(); renderView(); status.textContent = state.notice; status.classList.toggle("error", state.error);
      if (focusedId && !busy) (page.querySelector('[data-tag-id="' + CSS.escape(focusedId) + '"] ' + focusedControl) || page).focus({ preventScroll: true });
      if (busy) setBusy(true);
    }
    function renderHistory(operations) {
      state.history = operations || []; historyList.replaceChildren();
      if (!state.history.length) { historyList.append(element("p", "tag-directory-hint", "还没有标签修改记录。")); return; }
      state.history.forEach((operation) => {
        const row = element("div", "tag-directory-history-row"), description = element("div"), name = historyNames[operation.action] || "标签操作";
        description.append(element("strong", "", name), element("small", "", new Date(operation.created_at).toLocaleString()));
        const undo = button(operation.undone ? "已撤销" : "撤销", () => run(() => options.request("/api/library/tags/undo", { method: "POST", body: JSON.stringify({ operation_id: operation.id }) }), "已撤销；之前取消的任务不会自动恢复"));
        undo.disabled = !operation.can_undo; if (!operation.can_undo && !operation.undone) undo.title = "后续已有修改，当前不能撤销覆盖";
        row.append(description, undo); historyList.append(row);
      }); if (state.busy) historyList.querySelectorAll("button").forEach((control) => { control.dataset.busyWasDisabled = control.disabled ? "1" : "0"; control.disabled = true; });
    }
    async function deleteSelected() {
      const tagIds = [...state.selected], names = selectedTags().slice(0, 6).map((tag) => tag.name).join("、") + (tagIds.length > 6 ? "等" : ""); if (!tagIds.length) return;
      await run(async () => {
        const impact = await options.request("/api/library/tags/impact?action=delete_tags&tag_ids=" + encodeURIComponent(tagIds.join(",")));
        if (!window.confirm("删除所选 " + tagIds.length + " 个标签（" + names + "）？\n将从 " + (impact.question_count || 0) + " 道当前题和 " + (impact.historical_count || 0) + " 道历史版本中移除关联。题面、答案和解析保留，可在操作记录中撤销。")) return false;
        return options.action("delete_tags", { tag_ids: tagIds, impact_token: impact.token, confirmed: true });
      }, "所选标签已删除，可在操作记录中撤销");
    }
    function openMergeDialog() {
      if (state.busy) return;
      const sources = selectedTags(); if (!sources.length) return;
      // 1.25.38：「全选当前结果」之后能一次勾 300 个，合并方向选反就收拾不回来。
      // 超过 20 个先缩小范围，再做这一步。
      if (sources.length > 20) { setNotice("一次最多合并 20 个标签；请先按分类或搜索缩小范围。", true); return; }
      const dialog = element("dialog", "tag-directory-merge-dialog"), title = element("h2", "", "合并标签"), targetLabel = element("label", "", "保留哪个标签？"), target = element("select");
      target.id = "tagManagerMergeTarget"; target.add(new Option("选择保留的标签…", ""));
      (state.catalogue.tags || []).filter((tag) => tag.active !== false && sources.every((source) => source.id === tag.id || compatible(source, tag)) && !(sources.length === 1 && sources[0].id === tag.id))
        .sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN")).forEach((tag) => target.add(new Option(tag.name + "（" + (tag.count || 0) + " 题）", tag.id)));
      targetLabel.append(target); const mergeStatus = element("p", "tag-directory-hint"); mergeStatus.setAttribute("role", "status");
      mergeStatus.textContent = "保留目标标签；其余所选标签及题目关联合并到目标，重复关联会整理。";
      const actions = element("footer"), cancel = button("取消", () => dialog.close()), submit = button("合并", async () => {
        const targetId = target.value, sourceIds = sources.filter((tag) => String(tag.id) !== targetId).map((tag) => tag.id);
        if (!targetId || !sourceIds.length) { mergeStatus.textContent = "请选择一个保留的标签。"; return; }
        submit.disabled = cancel.disabled = target.disabled = true;
        const succeeded = await run(async () => {
          const impact = await options.request("/api/library/tags/impact?action=merge_tags_batch&source_ids=" + encodeURIComponent(sourceIds.join(",")) + "&target_id=" + encodeURIComponent(targetId));
          const targetTag = (state.catalogue.tags || []).find((tag) => String(tag.id) === targetId);
          if (!window.confirm("把 " + sourceIds.length + " 个标签合并到「" + targetTag.name + "」？\n影响 " + (impact.question_count || 0) + " 道当前题和 " + (impact.historical_count || 0) + " 道历史版本。原标签名称和来源会保留在历史中，操作可撤销。")) return false;
          const result = await options.action("merge_tags_batch", { source_ids: sourceIds, target_id: targetId, impact_token: impact.token, confirmed: true });
          state.selected.clear(); state.selected.add(targetId); state.focused = targetId; return result;
        }, "已合并标签，可在操作记录中撤销");
        if (succeeded) dialog.close(); else { submit.disabled = cancel.disabled = target.disabled = false; mergeStatus.textContent = state.notice || "选择已保留。"; }
      }, "button button-primary");
      submit.id = "tagManagerMergeSubmit"; actions.append(cancel, submit); dialog.append(title, targetLabel, mergeStatus, actions); document.body.append(dialog);
      dialog.addEventListener("cancel", (event) => { if (state.busy) event.preventDefault(); }); dialog.addEventListener("close", () => dialog.remove());
      dialog.showModal(); target.focus();
    }
    search.addEventListener("input", (event) => { if (event.isComposing) return; state.query = search.value; render(); });
    search.addEventListener("compositionend", () => { state.query = search.value; render(); });
    selectAll.addEventListener("change", () => { state.shown.forEach((tag) => { if (selectAll.checked) state.selected.add(String(tag.id)); else state.selected.delete(String(tag.id)); }); state.focused = state.shown.find((tag) => state.selected.has(String(tag.id)))?.id || ""; render(); });
    page.addEventListener("keydown", (event) => {
      if (!state.open || state.busy || event.isComposing || document.querySelector("dialog[open]") || event.target.closest("input, textarea, select, [contenteditable=true]")) return;
      if (state.view === "map" && event.key !== "Escape" && event.target.closest(".tag-map-viewport")) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (state.selected.size || state.focused) { state.selected.clear(); state.focused = ""; render(); } else close(); }
      else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") { event.preventDefault(); state.shown.forEach((tag) => state.selected.add(String(tag.id))); render(); }
      else if (["ArrowDown", "ArrowUp"].includes(event.key) && state.shown.length) {
        event.preventDefault(); const current = state.shown.findIndex((tag) => String(tag.id) === String(state.focused)), next = current < 0 ? 0 : Math.max(0, Math.min(state.shown.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)));
        choose(state.shown[next], event); page.querySelector('[data-tag-id="' + CSS.escape(String(state.shown[next].id)) + '"]')?.scrollIntoView({ block: "nearest" });
      }
      else if (event.key === "ArrowRight") {
        const tag = tagById(state.focused); if (tag && childrenOf(tag).length) { event.preventDefault(); browseHierarchy(tag); }
      }
      else if (event.key === "ArrowLeft" && state.hierarchyNode) { event.preventDefault(); goUp(); }
    });
    render(); renderHistory([]);
    return { get open() { return state.open; }, show, close, render, renderHistory };
  }
  root.QBLibraryTagManager = Object.freeze({ create });
})(window);
