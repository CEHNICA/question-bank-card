/* The user's tag directory lives in a page, separately from a question's tag picker. */
((root) => {
  "use strict";
  const kinds = { knowledge: "知识点", method: "解题方法", custom: "自定义", legacy: "历史标签" };
  const historyNames = { assign: "人工贴标", "suggestion:accept": "采纳 AI 建议", "suggestion:reject": "拒绝 AI 建议",
    "catalogue:create_tag": "新建标签", "catalogue:update_tag": "修改标签", "catalogue:favorite_tag": "收藏调整",
    "catalogue:activate_tag": "启用标签", "catalogue:deactivate_tag": "停用标签", "catalogue:merge_tags": "合并标签",
    "catalogue:merge_tags_batch": "批量合并标签", "catalogue:create_group": "新建分组", "catalogue:update_group": "修改分组",
    "catalogue:group_membership": "调整标签分组", "catalogue:group_membership_batch": "批量调整分组",
    "catalogue:delete_group": "删除分组（保留标签）", "catalogue:delete_tag": "删除标签", "catalogue:delete_tags": "批量删除标签",
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
  function memberships(tag) { return (tag.groups || []).map((group) => typeof group === "string" ? group : String(group.id)); }
  function compatible(source, target) { return source.kind === target.kind || (source.kind === "legacy" && ["knowledge", "custom"].includes(target.kind)); }

  function create(options) {
    const mount = options.mount;
    if (!mount) throw new Error("标签管理缺少页面容器");
    const state = { open: false, busy: false, group: "all", query: "", selected: new Set(), focused: "", anchor: "", hierarchyKind: "", hierarchyNode: "",
      relations: new Map(), relationLoads: new Map(),
      catalogue: { groups: [], tags: [] }, shown: [], history: [], notice: "", error: false, scroll: 0, original: null, originalDisplay: "", originalHidden: false };
    const page = element("section", "tag-directory-workspace"); page.id = "tagManagerWorkspace"; page.hidden = true; page.tabIndex = -1;
    page.setAttribute("aria-labelledby", "tagManagerHeading");
    const header = element("header", "tag-directory-header"), headerText = element("div"), heading = element("h1", "", "标签管理");
    heading.id = "tagManagerHeading"; const total = element("span", "tag-directory-total"); total.id = "tagManagerTotalCount";
    headerText.append(heading, total);
    const back = button("← 返回题库", close, "button button-quiet"); back.id = "tagManagerBack"; header.append(headerText, back);
    const toolbar = element("div", "tag-directory-toolbar"), searchBox = element("label", "tag-directory-search"), searchLabel = element("span", "sr-only", "搜索标签");
    const search = element("input"); search.id = "tagManagerSearch"; search.type = "search"; search.placeholder = "搜索标签、别名或说明"; search.setAttribute("aria-label", "搜索标签、别名或说明");
    searchBox.append(searchLabel, search);
    const newTag = button("＋ 新建标签", () => options.editEntity("tag", null, { group_id: currentGroup()?.id,
      ...(state.hierarchyKind ? { kind: state.hierarchyKind } : {}), ...(state.hierarchyNode ? { parent_ids: [state.hierarchyNode] } : {}) }), "button button-primary"); newTag.id = "tagManagerCreateTag";
    const templates = button("追加模板", () => options.openTemplates()); templates.id = "tagManagerTemplates";
    const advanced = element("details", "tag-directory-advanced"), advancedTitle = element("summary", "button button-small", "更多"), advancedMenu = element("div", "tag-directory-menu");
    const migrationReport = element("a", "button button-quiet button-small", "导出旧标签映射");
    migrationReport.id = "tagManagerMigrationReport"; migrationReport.href = "/api/library/tags/migration-report";
    migrationReport.setAttribute("download", "");
    const reset = button("清空标签目录", () => run(() => options.clearCatalogue(), "目录操作已完成"), "button button-quiet button-danger"); reset.id = "tagManagerReset";
    advancedMenu.append(migrationReport, reset, element("small", "", "清空全库标签与题目关联，提交前会显示影响范围。")); advanced.append(advancedTitle, advancedMenu);
    toolbar.append(searchBox, newTag, templates, advanced);
    const status = element("p", "tag-directory-status"); status.id = "tagManagerStatus"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const body = element("div", "tag-directory-body"), groupsPanel = element("aside", "tag-directory-groups"), groups = element("nav", "tag-directory-group-list");
    groups.id = "tagManagerGroups"; groups.setAttribute("aria-label", "标签分组");
    const groupActions = element("div", "tag-directory-group-actions"); groupActions.id = "tagManagerGroupActions";
    const hierarchyHeading = element("h2", "tag-directory-nav-heading", "标签层级"), hierarchyNav = element("nav", "tag-directory-hierarchy-nav");
    hierarchyNav.id = "tagManagerHierarchyNav"; hierarchyNav.setAttribute("aria-label", "知识点和方法层级");
    const hierarchyHint = element("p", "tag-directory-nav-hint", "父级和子级都能贴题，点击箭头逐层浏览。");
    const groupHeading = element("div", "tag-directory-group-heading"); groupHeading.append(element("span", "", "个人分组"), button("＋", () => options.editEntity("group"), "tag-directory-add-group"));
    groupHeading.lastChild.title = "新建分组"; groupHeading.lastChild.setAttribute("aria-label", "新建分组"); groupHeading.lastChild.id = "tagManagerCreateGroup";
    groupsPanel.append(hierarchyHeading, hierarchyNav, hierarchyHint, groupHeading, groups, groupActions,
      element("p", "tag-directory-nav-hint", "分组只用于个人整理，不代表知识上下级。"));
    const results = element("section", "tag-directory-results"), listHeading = element("div", "tag-directory-list-heading"), selectAll = element("input"); selectAll.type = "checkbox";
    selectAll.id = "tagManagerSelectAll"; selectAll.setAttribute("aria-label", "选择当前显示的全部标签");
    const selectAllLabel = element("label", "tag-directory-select-all"); selectAllLabel.append(selectAll, element("span", "", "全选当前结果"));
    const count = element("span", "tag-directory-visible-count"); count.id = "tagManagerVisibleCount"; listHeading.append(selectAllLabel, count);
    const bulk = element("div", "tag-directory-bulk"); bulk.id = "tagManagerBulk"; bulk.hidden = true;
    const selectedCount = element("strong"), hiddenSelection = element("small", "tag-directory-hidden-selection"), groupSelect = element("select"); groupSelect.id = "tagManagerBulkGroup"; groupSelect.setAttribute("aria-label", "批量整理的目标分组");
    const addToGroup = button("加入分组", () => changeMembership("add")), removeFromGroup = button("移出分组", () => changeMembership("remove"));
    addToGroup.id = "tagManagerBulkAdd"; removeFromGroup.id = "tagManagerBulkRemove";
    const merge = button("合并标签", openMergeDialog), remove = button("删除标签", deleteSelected, "button button-small button-danger");
    merge.id = "tagManagerBulkMerge"; remove.id = "tagManagerBulkDelete";
    const clearSelection = button("取消选择", () => { state.selected.clear(); state.focused = ""; render(); }, "button button-quiet button-small");
    bulk.append(selectedCount, hiddenSelection, groupSelect, addToGroup, removeFromGroup, merge, remove, clearSelection);
    const location = element("div", "tag-directory-location"); location.id = "tagManagerLocation";
    const list = element("div", "tag-directory-list"); list.id = "tagManagerList"; list.setAttribute("aria-label", "标签列表");
    results.append(location, listHeading, bulk, list);
    const detail = element("aside", "tag-directory-detail"); detail.id = "tagManagerDetail"; detail.setAttribute("aria-label", "选中标签的信息");
    body.append(groupsPanel, results, detail);
    const history = element("details", "tag-directory-history"), historySummary = element("summary", "", "操作记录与撤销"), historyList = element("div", "tag-directory-history-list");
    historyList.id = "tagManagerHistoryList"; history.append(historySummary, historyList);
    page.append(header, toolbar, status, body, history); mount.append(page);

    function currentGroup() { return (state.catalogue.groups || []).find((group) => String(group.id) === state.group); }
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
      if (state.busy || !["knowledge", "method"].includes(tag.kind)) return;
      state.hierarchyKind = tag.kind; state.hierarchyNode = String(tag.id); state.group = "all"; state.query = ""; search.value = "";
      render(); list.scrollTop = 0; location.querySelector("button")?.focus({ preventScroll: true });
    }
    async function ensureRelations(tag) {
      if (!["knowledge", "method"].includes(tag.kind) || !options.request) return;
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
        if (String(state.focused) === id) renderDetail();
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
      finally { setBusy(false); }
    }
    async function show() {
      if (state.open) { search.focus(); return; }
      state.scroll = window.scrollY; state.original = mount.querySelector(".library-workspace");
      if (state.original) { state.originalDisplay = state.original.style.display; state.originalHidden = state.original.hidden; state.original.hidden = true; state.original.style.display = "none"; }
      state.open = true; page.hidden = false; document.body.classList.add("library-tag-manager-open"); window.scrollTo(0, 0);
      setBusy(true); setNotice("正在载入标签目录…");
      try { render(await options.loadCatalogue()); setNotice(""); await reloadHistory(); }
      catch (error) { setNotice("目录暂时无法载入：" + error.message, true); }
      finally { setBusy(false); if (state.open) search.focus({ preventScroll: true }); }
    }
    function close() {
      if (!state.open || state.busy) return;
      state.open = false; page.hidden = true; document.body.classList.remove("library-tag-manager-open");
      if (state.original) { state.original.hidden = state.originalHidden; state.original.style.display = state.originalDisplay; }
      window.scrollTo(0, state.scroll); options.onClose?.();
    }
    function choose(tag, event = {}) {
      const id = String(tag.id);
      if (event.shiftKey && state.anchor) {
        const from = state.shown.findIndex((value) => String(value.id) === state.anchor), to = state.shown.findIndex((value) => String(value.id) === id);
        if (from >= 0 && to >= 0) state.shown.slice(Math.min(from, to), Math.max(from, to) + 1).forEach((value) => state.selected.add(String(value.id)));
        else state.selected.add(id);
      } else if (event.ctrlKey || event.metaKey) { if (state.selected.has(id)) state.selected.delete(id); else state.selected.add(id); state.anchor = id; }
      else { state.selected.clear(); state.selected.add(id); state.anchor = id; }
      state.focused = id; render();
      void ensureRelations(tag);
      page.querySelector('[data-tag-id="' + CSS.escape(id) + '"] .tag-directory-name')?.focus({ preventScroll: true });
    }
    function renderGroups() {
      const scroll = groups.scrollTop, tags = state.catalogue.tags || []; groups.replaceChildren();
      hierarchyNav.replaceChildren();
      for (const kind of ["knowledge", "method"]) {
        const control = button("", () => { state.hierarchyKind = kind; state.hierarchyNode = ""; state.group = "all"; render(); list.scrollTop = 0; },
          "tag-directory-group" + (state.hierarchyKind === kind ? " active" : ""));
        control.dataset.hierarchyKind = kind; control.setAttribute("aria-pressed", String(state.hierarchyKind === kind));
        control.append(element("span", "", kinds[kind]), element("small", "", tags.filter((tag) => tag.kind === kind).length)); hierarchyNav.append(control);
      }
      const add = (id, name, number) => {
        const control = button("", () => { state.group = id; state.hierarchyKind = ""; state.hierarchyNode = ""; render(); groups.querySelector('[data-group-id="' + CSS.escape(id) + '"]')?.focus({ preventScroll: true }); }, "tag-directory-group" + (state.group === id && !state.hierarchyKind ? " active" : ""));
        control.dataset.groupId = id; control.setAttribute("aria-pressed", String(state.group === id && !state.hierarchyKind));
        control.append(element("span", "", name), element("small", "", number)); groups.append(control);
      };
      add("all", "全部标签", tags.length); add("ungrouped", "未分组", tags.filter((tag) => !memberships(tag).length).length); add("favorite", "收藏", tags.filter((tag) => tag.favorite).length);
      if ((state.catalogue.groups || []).length) groups.append(element("div", "tag-directory-group-divider"));
      (state.catalogue.groups || []).forEach((group) => add(String(group.id), group.name, tags.filter((tag) => memberships(tag).includes(String(group.id))).length));
      groups.scrollTop = scroll; groupActions.replaceChildren();
      const current = currentGroup();
      if (current) {
        groupActions.append(element("strong", "", current.name), button("编辑分组", () => options.editEntity("group", current), "button button-quiet button-small"),
          button("删除分组（保留标签）", () => {
            if (!window.confirm("删除分组「" + current.name + "」？标签和题目关联会保留；没有其他分组的标签进入“未分组”。")) return;
            void run(async () => { const result = await options.action("delete_group", { group_id: current.id }); state.group = "all"; return result; });
          }, "button button-quiet button-small"));
      }
    }
    function renderLocation() {
      location.replaceChildren(); location.hidden = !state.hierarchyKind;
      if (!state.hierarchyKind) return;
      const current = tagById(state.hierarchyNode), home = button(kinds[state.hierarchyKind] + " · 顶级", () => { state.hierarchyNode = ""; render(); }, "tag-directory-path-button");
      location.append(home);
      if (current) {
        const paths = parentPaths(current), pathList = element("div", "tag-directory-paths");
        paths.forEach((path) => {
          const row = element("div", "tag-directory-path");
          path.forEach((tag, index) => { if (index) row.append(element("span", "tag-directory-path-separator", "›"));
            const control = button(tag.name, () => browseHierarchy(tag), "tag-directory-path-button" + (String(tag.id) === String(current.id) ? " current" : ""));
            control.dataset.pathTagId = tag.id; row.append(control); }); pathList.append(row);
        });
        location.append(pathList, element("small", "", state.query ? "搜索当前类型的全部标签" : "正在查看「" + current.name + "」的直接子标签"));
        location.append(button("选择当前标签", () => choose(current), "button button-small"),
          button("＋ 添加子标签", () => options.editEntity("tag", null, { parent_ids: [current.id], kind: current.kind }), "button button-small"));
      } else location.append(element("small", "", state.query ? "搜索当前类型的全部标签" : "展开箭头查看子标签；每一级都可以贴题。"));
    }
    function renderList() {
      const scroll = list.scrollTop, query = normalized(state.query.trim());
      state.shown = (state.catalogue.tags || []).filter((tag) => {
        if (state.hierarchyKind) {
          if (tag.kind !== state.hierarchyKind) return false;
          if (!query) {
            if (state.hierarchyNode && !relationIds(tag, "parent_ids").includes(String(state.hierarchyNode))) return false;
            if (!state.hierarchyNode && parentsOf(tag).some((parent) => parent.kind === tag.kind)) return false;
          }
        }
        const memberIds = memberships(tag);
        if (state.group === "ungrouped" && memberIds.length) return false;
        if (state.group === "favorite" && !tag.favorite) return false;
        if (!["all", "ungrouped", "favorite"].includes(state.group) && !memberIds.includes(state.group)) return false;
        const searchText = normalized([tag.name, tag.description, tag.conditions, ...(tag.aliases || []), ...(tag.groups || []).map((group) => group.name || ""),
          ...parentPaths(tag).map((path) => path.map((node) => node.name).join(" "))].join(" "));
        return !query || query.split(/\s+/).every((part) => searchText.includes(part));
      }).sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN", { numeric: true }));
      list.replaceChildren(); count.textContent = state.shown.length + " 个标签";
      selectAll.checked = state.shown.length > 0 && state.shown.every((tag) => state.selected.has(String(tag.id)));
      selectAll.indeterminate = !selectAll.checked && state.shown.some((tag) => state.selected.has(String(tag.id))); selectAll.disabled = !state.shown.length;
      if (!state.shown.length) {
        const empty = element("div", "tag-directory-empty"); empty.append(element("strong", "", state.query ? "没有找到这个标签" : "这里还没有标签"),
          element("p", "", state.query ? "换个名称、别名或说明搜索。" : state.hierarchyNode ? "这个标签已经可以贴题；需要细化时可添加子标签。" : "可以新建自己的标签，也可以追加需要的模板。")); list.append(empty);
      }
      state.shown.forEach((tag) => {
        const id = String(tag.id), row = element("div", "tag-directory-row" + (state.selected.has(id) ? " selected" : "") + (tag.active === false ? " inactive" : "")); row.dataset.tagId = id;
        const check = element("input"); check.type = "checkbox"; check.checked = state.selected.has(id); check.setAttribute("aria-label", "选择标签「" + tag.name + "」");
        check.addEventListener("change", () => { if (check.checked) { state.selected.add(id); state.focused = id; } else { state.selected.delete(id); if (state.focused === id) state.focused = ""; } state.anchor = id; render();
          page.querySelector('[data-tag-id="' + CSS.escape(id) + '"] > input')?.focus({ preventScroll: true }); });
        const dot = element("span", "tag-directory-color"); dot.setAttribute("aria-hidden", "true"); if (/^#[0-9a-f]{6}$/i.test(tag.color || "")) dot.style.backgroundColor = tag.color;
        const name = button(tag.name, (event) => choose(tag, event), "tag-directory-name"); name.title = tag.description || tag.name;
        const type = element("small", "tag-directory-kind", (tag.favorite ? "★ " : "") + (kinds[tag.kind] || "标签") + (tag.active === false ? " · 停用" : ""));
        const usage = button(directCount(tag) + (childrenOf(tag).length ? " / " + subtreeCount(tag) : "") + " 题", () => { if (state.busy) return; close(); options.browseTag(tag); }, "tag-directory-usage");
        usage.title = "本级 " + directCount(tag) + " 题，包含下级 " + subtreeCount(tag) + " 题；点击查看含下级的题目";
        const branch = childrenOf(tag).length && ["knowledge", "method"].includes(tag.kind) ? button("›", () => browseHierarchy(tag), "tag-directory-expand") : element("span", "tag-directory-expand-placeholder");
        if (branch.tagName === "BUTTON") { branch.title = "查看「" + tag.name + "」的子标签"; branch.setAttribute("aria-label", branch.title); branch.dataset.expandTagId = id; }
        row.append(check, dot, name, type, usage, branch); list.append(row);
      }); list.scrollTop = scroll;
      bulk.hidden = !state.selected.size; selectedCount.textContent = "已选 " + state.selected.size + " 个";
      const hiddenCount = state.selected.size - state.shown.filter((tag) => state.selected.has(String(tag.id))).length;
      hiddenSelection.textContent = hiddenCount ? "其中 " + hiddenCount + " 个不在当前结果中" : "";
      const previousGroup = groupSelect.value; groupSelect.replaceChildren(new Option("选择分组…", ""));
      (state.catalogue.groups || []).forEach((group) => groupSelect.add(new Option(group.name, group.id)));
      if ((state.catalogue.groups || []).some((group) => String(group.id) === previousGroup)) groupSelect.value = previousGroup;
      else if (currentGroup()) groupSelect.value = currentGroup().id;
      addToGroup.disabled = removeFromGroup.disabled = !groupSelect.value;
      merge.disabled = selectedTags().length < 1;
    }
    function info(title, text) { const row = element("div", "tag-directory-info"); row.append(element("span", "", title), element("p", "", text || "—")); return row; }
    function renderRelations(tag) {
      const block = element("section", "tag-directory-relations"); block.id = "tagManagerRelations";
      const heading = element("div", "tag-directory-relation-heading"); heading.append(element("h3", "", tag.kind === "method" ? "方法上下级" : "知识上下级"),
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
      const addChild = button("＋ 添加子标签", () => options.editEntity("tag", null, { parent_ids: [tag.id], kind: tag.kind,
        ...(currentGroup() ? { group_id: currentGroup().id } : {}) }), "button button-small");
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
        methodMode ? "。相关方法独立于知识上下级，不会自动给题目添加标签。" : "。个人分组保持独立；自己和后代不能作为父级。"));
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
      const selected = selectedTags(), tag = selected.find((value) => String(value.id) === state.focused) || selected[0];
      if (!tag) { detail.append(element("h2", "", "标签信息"), element("p", "tag-directory-hint", "点一个标签查看说明和所属分组；勾选多个标签可以一起整理。")); return; }
      if (selected.length > 1) {
        detail.append(element("h2", "", "已选 " + selected.length + " 个标签"), element("p", "tag-directory-hint", "上方操作只作用于已选标签。加入分组会保留原分组，移出分组不会删除标签。"));
        const groupInfo = element("div", "tag-directory-shared-groups"); groupInfo.append(element("h3", "", "所属分组"));
        (state.catalogue.groups || []).forEach((group) => { const number = selected.filter((value) => memberships(value).includes(String(group.id))).length; if (number) groupInfo.append(info(group.name, number === selected.length ? "全部都有" : number + " / " + selected.length + " 个标签")); });
        if (groupInfo.childElementCount === 1) groupInfo.append(element("p", "tag-directory-hint", "所选标签均未分组。"));
        detail.append(groupInfo); return;
      }
      const title = element("div", "tag-directory-detail-title"); title.append(element("h2", "", tag.name), element("small", "", kinds[tag.kind] || "标签")); detail.append(title);
      const edit = button("编辑标签", () => options.editEntity("tag", tag), "button button-primary"); edit.id = "tagManagerEditTag";
      const usage = button("本级 " + directCount(tag) + " 题 · 含下级 " + subtreeCount(tag) + " 题 →", () => { close(); options.browseTag(tag); }, "tag-directory-detail-usage");
      detail.append(edit, usage, info("说明", tag.description), info("别名", (tag.aliases || []).join("、")));
      if (["knowledge", "method"].includes(tag.kind)) renderRelations(tag);
      const groupBlock = element("div", "tag-directory-detail-groups"); groupBlock.append(element("h3", "", "所属分组"));
      if (!memberships(tag).length) groupBlock.append(element("p", "tag-directory-hint", "未分组"));
      (state.catalogue.groups || []).filter((group) => memberships(tag).includes(String(group.id))).forEach((group) => groupBlock.append(button(group.name, () => { state.group = String(group.id); render(); }, "tag-directory-group-chip")));
      detail.append(groupBlock);
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
      if (!["all", "ungrouped", "favorite"].includes(state.group) && !currentGroup()) state.group = "all";
      const busy = state.busy; if (busy) setBusy(false);
      total.textContent = (state.catalogue.tags || []).length + " 个标签";
      renderGroups(); renderLocation(); renderList(); renderDetail(); status.textContent = state.notice; status.classList.toggle("error", state.error);
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
    function changeMembership(mode) {
      const groupId = groupSelect.value, tagIds = [...state.selected]; if (!groupId || !tagIds.length) return;
      void run(() => options.action("group_membership_batch", { tag_ids: tagIds, group_id: groupId, mode }), mode === "add" ? "已加入分组，原有分组保留" : "已移出分组，标签和题目关联保留");
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
          if (!window.confirm("把 " + sourceIds.length + " 个标签合并到「" + targetTag.name + "」？\n影响 " + (impact.question_count || 0) + " 道当前题和 " + (impact.historical_count || 0) + " 道历史版本。原标签名称、分组和来源会保留在历史中，操作可撤销。")) return false;
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
    groupSelect.addEventListener("change", () => { addToGroup.disabled = removeFromGroup.disabled = !groupSelect.value; });
    page.addEventListener("keydown", (event) => {
      if (!state.open || state.busy || event.isComposing || document.querySelector("dialog[open]") || event.target.closest("input, textarea, select, [contenteditable=true]")) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (state.selected.size) { state.selected.clear(); state.focused = ""; render(); } else close(); }
      else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") { event.preventDefault(); state.shown.forEach((tag) => state.selected.add(String(tag.id))); render(); }
      else if (["ArrowDown", "ArrowUp"].includes(event.key) && state.shown.length) {
        event.preventDefault(); const current = state.shown.findIndex((tag) => String(tag.id) === String(state.focused)), next = current < 0 ? 0 : Math.max(0, Math.min(state.shown.length - 1, current + (event.key === "ArrowDown" ? 1 : -1)));
        choose(state.shown[next], event); page.querySelector('[data-tag-id="' + CSS.escape(String(state.shown[next].id)) + '"]')?.scrollIntoView({ block: "nearest" });
      }
      else if (event.key === "ArrowRight") {
        const tag = tagById(state.focused); if (tag && childrenOf(tag).length) { event.preventDefault(); browseHierarchy(tag); }
      }
      else if (event.key === "ArrowLeft" && state.hierarchyNode) {
        const tag = tagById(state.hierarchyNode), parents = tag ? parentsOf(tag) : []; event.preventDefault();
        if (parents.length === 1) browseHierarchy(parents[0]);
        else if (!parents.length) { state.hierarchyNode = ""; render(); }
        else setNotice("这个标签有多个父级，请从上方路径或右侧父标签中选择要前往的位置。");
      }
    });
    render(); renderHistory([]);
    return { get open() { return state.open; }, get activeGroup() { return state.group; }, show, close, render, renderHistory };
  }
  root.QBLibraryTagManager = Object.freeze({ create });
})(window);
