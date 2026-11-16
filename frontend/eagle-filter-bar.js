/*
 * Eagle 风格多维紧凑折叠胶囊筛选器 & 智能工作台系统 (Eagle Filter Bar)
 * 100% 深度融合题有据，完全还原 preview.html 的 Clean UI 质感与交互体系
 */
(() => {
  "use strict";

  const TYPE_NAMES = {
    single_choice: "单选题",
    multiple_choice: "多选题",
    fill_blank: "填空题",
    true_false: "判断题",
    free_response: "解答题"
  };

  const DIRECTORY_KINDS = {
    all: "全部",
    knowledge: "知识点",
    method: "解题方法",
    custom: "自定义"
  };

  const SMART_FOLDER_KEY = "qb-smart-folders-v2";
  const LEGACY_SAVED_KEY = "qb-library-saved-tag-filters";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // 解析接口返回的是对象（{solution:{answer,analysis,...}}），旧代码把对象当字符串用，
  // 结果明明有解析也显示「未能读取到原卷解析」。这里只负责把正文取成**纯文本字符串**，
  // 渲染一律走 textContent，不当 HTML 解析。
  function extractSolutionText(payload) {
    if (!payload || typeof payload !== "object") return "";
    const solution = payload.solution && typeof payload.solution === "object" ? payload.solution : null;
    const pick = (value) => (typeof value === "string" && value.trim() ? value : "");
    return pick(solution && solution.analysis)
      || pick(solution && solution.answer)
      || pick(payload.analysis)
      || pick(payload.content && payload.content.analysis)
      || pick(payload.content && payload.content.answer)
      || "";
  }

  function safeToast(message, type = "success") {
    if (typeof window.toast === "function") {
      window.toast(message, type);
    } else {
      const box = document.getElementById("toast");
      if (box) {
        box.textContent = message;
        box.className = `toast ${type}`.trim();
        try { if (typeof box.showPopover === "function") box.showPopover(); } catch (_) { box.hidden = false; }
        setTimeout(() => { try { if (typeof box.hidePopover === "function") box.hidePopover(); } catch (_) { box.hidden = true; } }, 2500);
      }
    }
  }

  function getSmartFolders() {
    try {
      let folders = JSON.parse(localStorage.getItem(SMART_FOLDER_KEY) || "null");
      if (!folders || !Array.isArray(folders)) {
        folders = [
          { id: "sf_preset_1", name: "近三年高考精选", filter: { tagMode: "all", q: "高考", sort: "recent" } },
          { id: "sf_preset_2", name: "待审核 AI 题", filter: { tagReview: true, sort: "recent" } },
          { id: "sf_preset_3", name: "解答题专练", filter: { type: "free_response", sort: "recent" } }
        ];
        // 自动平滑迁移旧版的常用标签组合
        const legacy = JSON.parse(localStorage.getItem(LEGACY_SAVED_KEY) || "[]");
        if (Array.isArray(legacy) && legacy.length) {
          legacy.forEach(item => {
            if (item && item.name && item.filter) {
              folders.push({
                id: "migrated_" + Math.random().toString(36).slice(2, 8),
                name: item.name,
                filter: {
                  tagMode: item.filter.mode || "all",
                  tagAll: item.filter.all || "",
                  tagAny: item.filter.any || "",
                  tagNot: item.filter.not || ""
                }
              });
            }
          });
        }
        localStorage.setItem(SMART_FOLDER_KEY, JSON.stringify(folders));
      }
      return folders;
    } catch (_) {
      return [];
    }
  }

  function saveSmartFolder(name, currentFilter) {
    if (!name || !name.trim()) return false;
    const folders = getSmartFolders();
    const cleanName = name.trim();
    const existingIndex = folders.findIndex(f => f.name === cleanName);
    const newEntry = {
      id: "sf_" + Date.now().toString(36),
      name: cleanName,
      filter: Object.assign({}, currentFilter),
      createdAt: new Date().toISOString()
    };
    if (existingIndex >= 0) {
      folders[existingIndex] = newEntry;
    } else {
      folders.unshift(newEntry);
    }
    try {
      localStorage.setItem(SMART_FOLDER_KEY, JSON.stringify(folders.slice(0, 50)));
      return true;
    } catch (_) {
      return false;
    }
  }

  function deleteSmartFolder(id) {
    let folders = getSmartFolders();
    folders = folders.filter(f => f.id !== id);
    try {
      localStorage.setItem(SMART_FOLDER_KEY, JSON.stringify(folders));
      return true;
    } catch (_) {
      return false;
    }
  }

  class EagleFilterBar {
    constructor() {
      this.container = null;
      this.context = null; // { state, tagCatalogue, directory, onApplyFilter }
      this.activeDropdown = null;
      this.tagSearchQuery = "";
      this.tagGroupFilter = "all";
      this.sourceSearchQuery = "";
      this._handleGlobalClick = this._handleGlobalClick.bind(this);
      this._handleKeyDown = this._handleKeyDown.bind(this);
    }

    mount(parentEl, context) {
      this.context = context;
      if (!parentEl) return;

      const old = parentEl.querySelector("#eagleFilterBarRoot");
      if (old) old.remove();

      this.container = el("div", "eagle-filter-container");
      this.container.id = "eagleFilterBarRoot";

      // 胶囊按钮组
      const capsulesGroup = el("div", "eagle-capsules-group");

      // 1. 题型胶囊
      this.typeCapsule = this._createCapsuleWrapper("type", "题型: 全部", () => this._renderTypeDropdown());
      capsulesGroup.append(this.typeCapsule.wrapper);

      // 2. 标签胶囊 (核心 AND/OR/NOT)
      this.tagCapsule = this._createCapsuleWrapper("tag", "标签: 全部", () => this._renderTagDropdown());
      capsulesGroup.append(this.tagCapsule.wrapper);

      // 3. 来源试卷胶囊
      this.sourceCapsule = this._createCapsuleWrapper("source", "来源: 全部试卷", () => this._renderSourceDropdown());
      capsulesGroup.append(this.sourceCapsule.wrapper);

      // 4. 审核与答案胶囊
      this.statusCapsule = this._createCapsuleWrapper("status", "状态: 全部", () => this._renderStatusDropdown());
      capsulesGroup.append(this.statusCapsule.wrapper);

      // 5. 排序胶囊
      this.sortCapsule = this._createCapsuleWrapper("sort", "排序: 最近入库", () => this._renderSortDropdown());
      capsulesGroup.append(this.sortCapsule.wrapper);

      this.container.append(capsulesGroup);

      // 6. 内联 Active Chips 容器 (直接与胶囊并排在同一行)
      this.inlineChipsContainer = el("div", "eagle-inline-chips");
      this.container.append(this.inlineChipsContainer);

      // 7. 智能文件夹快速入口
      const smartFolderBox = el("div", "eagle-smart-folder-capsule");
      this.smartFolderCapsule = this._createSmartFolderWrapper();
      smartFolderBox.append(this.smartFolderCapsule.wrapper);
      this.container.append(smartFolderBox);

      // 插入到 parentEl
      parentEl.prepend(this.container);

      // 增强左侧栏：注入智能视图/章节大纲分段切换
      this._enhanceSidebar();

      // 全局事件
      document.removeEventListener("click", this._handleGlobalClick);
      document.removeEventListener("keydown", this._handleKeyDown);
      document.addEventListener("click", this._handleGlobalClick);
      document.addEventListener("keydown", this._handleKeyDown);

      // 卡片是分批渲染的：首屏一批，滚动加载一批一批来。
      // 不盯着列表容器的话，后来的卡片永远等不到「👁 查看解析」。
      this._observeCardList();

      this.syncUI();
    }

    _handleGlobalClick(event) {
      if (!this.activeDropdown) return;
      const inside = event.target.closest(".eagle-capsule-wrapper");
      if (!inside) {
        this.closeAll();
      }
    }

    _handleKeyDown(event) {
      if (event.key === "Escape" && this.activeDropdown) {
        this.closeAll();
      }
    }

    closeAll() {
      this.activeDropdown = null;
      if (!this.container) return;
      this.container.querySelectorAll(".eagle-capsule-btn").forEach(btn => btn.classList.remove("is-open"));
      this.container.querySelectorAll(".eagle-dropdown-panel").forEach(panel => panel.classList.remove("show"));
    }

    toggleDropdown(name, btn, panel) {
      if (this.activeDropdown === name) {
        this.closeAll();
        return;
      }
      this.closeAll();
      this.activeDropdown = name;
      btn.classList.add("is-open");
      panel.classList.add("show");
    }

    _createCapsuleWrapper(name, defaultLabel, renderFn) {
      const wrapper = el("div", "eagle-capsule-wrapper");
      const btn = el("button", "eagle-capsule-btn");
      btn.type = "button";

      const labelSpan = el("span", "eagle-capsule-label", defaultLabel);
      const arrowSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      arrowSvg.setAttribute("class", "eagle-capsule-arrow");
      arrowSvg.setAttribute("viewBox", "0 0 24 24");
      arrowSvg.innerHTML = '<path fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" d="m6 9 6 6 6-6"/>';

      btn.append(labelSpan, arrowSvg);

      const panel = el("div", "eagle-dropdown-panel");
      panel.dataset.capsuleName = name;

      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        renderFn();
        this.toggleDropdown(name, btn, panel);
      });

      wrapper.append(btn, panel);
      return { wrapper, btn, labelSpan, panel };
    }

    _createSmartFolderWrapper() {
      const wrapper = el("div", "eagle-capsule-wrapper");
      const btn = el("button", "eagle-smart-folder-btn");
      btn.type = "button";
      btn.innerHTML = '<span style="font-size:13px">📁</span><span>智能文件夹</span>';

      const panel = el("div", "eagle-dropdown-panel eagle-smart-folder-panel");

      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._renderSmartFolderDropdown();
        this.toggleDropdown("smartFolder", btn, panel);
      });

      wrapper.append(btn, panel);
      return { wrapper, btn, panel };
    }

    /* ──── 增强左侧栏：分段器与智能视图 ──── */
    _enhanceSidebar() {
      const rail = document.querySelector(".library-rail") || document.getElementById("libraryRail");
      if (!rail || rail.querySelector("#eagleSidebarEnhance")) return;

      const enhanceBox = el("div");
      enhanceBox.id = "eagleSidebarEnhance";
      enhanceBox.style.marginBottom = "10px";

      // 分段控制器
      const seg = el("div", "eagle-logic-segmented");
      seg.style.marginBottom = "8px";

      const btnSmart = el("button", "eagle-logic-btn active", "智能视图");
      btnSmart.type = "button";
      const btnChapter = el("button", "eagle-logic-btn", "教材目录");
      btnChapter.type = "button";

      seg.append(btnSmart, btnChapter);

      // 智能视图内容容器
      const smartContent = el("div", "eagle-smart-folder-list");
      smartContent.id = "eagleSidebarSmartList";
      smartContent.style.display = "flex";
      smartContent.style.flexDirection = "column";
      smartContent.style.gap = "4px";

      const renderSidebarSmart = () => {
        smartContent.replaceChildren();
        const folders = getSmartFolders();
        folders.forEach(f => {
          const item = el("div", "eagle-folder-item");
          item.style.padding = "6px 8px";
          item.style.borderRadius = "6px";
          item.style.background = "#ffffff";
          item.style.border = "1px solid #e2e8f0";

          const label = el("span", "eagle-tag-name", "📁 " + f.name);
          item.append(label);
          item.addEventListener("click", () => {
            this._applySmartFolder(f);
          });
          smartContent.append(item);
        });
      };
      renderSidebarSmart();

      const origTree = document.getElementById("libraryDirectoryWorkspace") || document.querySelector(".library-toc-tree");

      btnSmart.addEventListener("click", () => {
        btnSmart.classList.add("active");
        btnChapter.classList.remove("active");
        smartContent.style.display = "flex";
        if (origTree) origTree.style.display = "none";
      });

      btnChapter.addEventListener("click", () => {
        btnChapter.classList.add("active");
        btnSmart.classList.remove("active");
        smartContent.style.display = "none";
        if (origTree) origTree.style.display = "";
      });

      enhanceBox.append(seg, smartContent);
      rail.prepend(enhanceBox);
    }

    /* ──── 1. 题型下拉 ──── */
    _renderTypeDropdown() {
      const panel = this.typeCapsule.panel;
      panel.replaceChildren();
      panel.className = "eagle-dropdown-panel eagle-type-menu show";

      const state = this.context.state;
      const facets = state.facets?.types || {};

      const allItem = el("div", `eagle-menu-item${!state.type ? " selected" : ""}`);
      allItem.append(el("span", "", "全部题型"));
      allItem.addEventListener("click", () => {
        state.type = "";
        this._notifyFilterChanged();
        this.closeAll();
      });
      panel.append(allItem);

      Object.entries(TYPE_NAMES).forEach(([typeKey, typeLabel]) => {
        const count = facets[typeKey] || 0;
        const item = el("div", `eagle-menu-item${state.type === typeKey ? " selected" : ""}`);
        item.append(el("span", "", typeLabel));
        item.append(el("span", "eagle-item-count", String(count)));

        item.addEventListener("click", () => {
          state.type = state.type === typeKey ? "" : typeKey;
          this._notifyFilterChanged();
          this.closeAll();
        });
        panel.append(item);
      });
    }

    /* ──── 2. 标签核心下拉 (AND/OR/NOT) ──── */
    _renderTagDropdown() {
      const panel = this.tagCapsule.panel;
      panel.replaceChildren();
      panel.className = "eagle-dropdown-panel eagle-tag-panel show";

      const state = this.context.state;

      const header = el("div", "eagle-tag-header");
      const searchInput = el("input", "eagle-tag-search-input");
      searchInput.type = "search";
      searchInput.placeholder = "搜索标签 (中文 / 拼音)...";
      searchInput.value = this.tagSearchQuery;
      searchInput.addEventListener("input", (e) => {
        this.tagSearchQuery = e.target.value.trim().toLowerCase();
        this._refreshTagList(listContainer);
      });
      searchInput.addEventListener("click", (e) => e.stopPropagation());
      header.append(searchInput);

      const logicBox = el("div", "eagle-logic-segmented");
      const modes = [
        { key: "all", label: "包含全部 (AND)" },
        { key: "any", label: "包含任一 (OR)" },
        { key: "not", label: "排除这些 (NOT)" }
      ];

      modes.forEach(m => {
        const btn = el("button", `eagle-logic-btn${state.tagMode === m.key ? " active" : ""}`, m.label);
        btn.type = "button";
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          this._switchTagMode(m.key);
        });
        logicBox.append(btn);
      });
      header.append(logicBox);

      const tabsBox = el("div", "eagle-group-tabs");
      Object.entries(DIRECTORY_KINDS).forEach(([kindKey, kindLabel]) => {
        const tab = el("button", `eagle-group-tab${this.tagGroupFilter === kindKey ? " active" : ""}`, kindLabel);
        tab.type = "button";
        tab.addEventListener("click", (e) => {
          e.stopPropagation();
          this.tagGroupFilter = kindKey;
          tabsBox.querySelectorAll(".eagle-group-tab").forEach(t => t.classList.remove("active"));
          tab.classList.add("active");
          this._refreshTagList(listContainer);
        });
        tabsBox.append(tab);
      });
      header.append(tabsBox);
      panel.append(header);

      const listContainer = el("div", "eagle-tag-list");
      this._refreshTagList(listContainer);
      panel.append(listContainer);

      const footer = el("div", "eagle-tag-footer");
      footer.style.display = "flex";
      footer.style.justifyContent = "space-between";
      footer.style.paddingTop = "8px";
      footer.style.borderTop = "1px solid #f1f5f9";
      footer.style.fontSize = "11.5px";
      footer.style.color = "#64748b";

      const selectedIds = this._getSelectedTagIds();
      const statusSpan = el("span", "", `已选 ${selectedIds.length} 个标签`);
      const clearBtn = el("button", "eagle-clear-all-btn", "清除已选");
      clearBtn.type = "button";
      clearBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._clearSelectedTags();
      });
      footer.append(statusSpan, clearBtn);
      panel.append(footer);
    }

    _refreshTagList(listContainer) {
      listContainer.replaceChildren();
      const allTags = this._getAllTags();
      const selectedIds = new Set(this._getSelectedTagIds());

      const filtered = allTags.filter(tag => {
        if (this.tagGroupFilter !== "all" && tag.kind !== this.tagGroupFilter) return false;
        if (this.tagSearchQuery) {
          const name = (tag.name || "").toLowerCase();
          const pinyin = (tag.pinyin || "").toLowerCase();
          if (!name.includes(this.tagSearchQuery) && !pinyin.includes(this.tagSearchQuery)) return false;
        }
        return true;
      });

      if (!filtered.length) {
        listContainer.append(el("div", "eagle-menu-item", "无匹配标签"));
        return;
      }

      filtered.forEach(tag => {
        const isSelected = selectedIds.has(String(tag.id));
        const row = el("div", `eagle-tag-row${isSelected ? " selected" : ""}`);

        const left = el("div");
        left.style.display = "flex";
        left.style.alignItems = "center";
        left.style.gap = "7px";
        left.style.minWidth = "0";

        const dot = el("span", "eagle-tag-color-dot");
        dot.style.backgroundColor = tag.color || (tag.kind === "method" ? "#3b82f6" : "#10b981");
        const nameSpan = el("span", "eagle-tag-name", tag.name);
        left.append(dot, nameSpan);

        const right = el("span", "eagle-item-count", isSelected ? "✓" : (tag.count ? String(tag.count) : ""));

        row.append(left, right);

        row.addEventListener("click", (e) => {
          e.stopPropagation();
          this._toggleTagSelection(tag.id);
        });

        listContainer.append(row);
      });
    }

    _switchTagMode(newMode) {
      const state = this.context.state;
      if (state.tagMode === newMode) return;
      const currentSelected = this._getSelectedTagIds().join(",");
      state.tagMode = newMode;
      state.tagAll = "";
      state.tagAny = "";
      state.tagNot = "";
      state.tag = "";

      if (newMode === "not") state.tagNot = currentSelected;
      else if (newMode === "any") state.tagAny = currentSelected;
      else state.tagAll = currentSelected;

      this._renderTagDropdown();
      this._notifyFilterChanged();
    }

    _toggleTagSelection(tagId) {
      const state = this.context.state;
      const idStr = String(tagId);
      const selected = new Set(this._getSelectedTagIds());

      if (selected.has(idStr)) {
        selected.delete(idStr);
      } else {
        selected.add(idStr);
      }

      const joined = Array.from(selected).join(",");
      state.tagAll = "";
      state.tagAny = "";
      state.tagNot = "";
      state.tag = "";

      if (state.tagMode === "not") state.tagNot = joined;
      else if (state.tagMode === "any") state.tagAny = joined;
      else state.tagAll = joined;

      this._renderTagDropdown();
      this._notifyFilterChanged();
    }

    _clearSelectedTags() {
      const state = this.context.state;
      state.tagAll = "";
      state.tagAny = "";
      state.tagNot = "";
      state.tag = "";
      state.untaggedKnowledge = false;
      state.tagReview = false;
      this._renderTagDropdown();
      this._notifyFilterChanged();
    }

    _getSelectedTagIds() {
      const state = this.context.state;
      const raw = state.tagAll || state.tagAny || state.tagNot || state.tag || "";
      if (!raw) return [];
      return String(raw).split(",").map(s => s.trim()).filter(Boolean);
    }

    _getAllTags() {
      const cat = this.context.tagCatalogue?.tags || [];
      const dir = this.context.directory?.tags || [];
      const map = new Map();

      cat.forEach(t => map.set(String(t.id), t));
      dir.forEach(t => {
        if (!map.has(String(t.id))) map.set(String(t.id), t);
      });

      return Array.from(map.values()).sort((a, b) => String(a.name).localeCompare(String(b.name), "zh-CN"));
    }

    /* ──── 3. 来源试卷下拉 ──── */
    _renderSourceDropdown() {
      const panel = this.sourceCapsule.panel;
      panel.replaceChildren();
      panel.className = "eagle-dropdown-panel eagle-source-panel show";

      const searchInput = el("input", "eagle-tag-search-input");
      searchInput.type = "search";
      searchInput.placeholder = "搜索试卷出处...";
      searchInput.value = this.sourceSearchQuery;
      searchInput.addEventListener("input", (e) => {
        this.sourceSearchQuery = e.target.value.trim().toLowerCase();
        this._refreshSourceList(listContainer);
      });
      searchInput.addEventListener("click", (e) => e.stopPropagation());
      panel.append(searchInput);

      const listContainer = el("div", "eagle-source-list");
      listContainer.style.maxHeight = "240px";
      listContainer.style.overflowY = "auto";
      this._refreshSourceList(listContainer);
      panel.append(listContainer);
    }

    _refreshSourceList(listContainer) {
      listContainer.replaceChildren();
      const state = this.context.state;
      const sources = state.facets?.sources || [];

      const allItem = el("div", `eagle-menu-item${!state.document ? " selected" : ""}`);
      allItem.append(el("span", "", "全部试卷"));
      allItem.addEventListener("click", () => {
        state.document = "";
        this._notifyFilterChanged();
        this.closeAll();
      });
      listContainer.append(allItem);

      const filtered = sources.filter(s => {
        if (!this.sourceSearchQuery) return true;
        return (s.filename || "").toLowerCase().includes(this.sourceSearchQuery);
      });

      filtered.forEach(s => {
        const item = el("div", `eagle-menu-item${state.document === s.document_id ? " selected" : ""}`);
        item.append(el("span", "eagle-tag-name", s.filename));
        item.append(el("span", "eagle-item-count", `${s.count}题`));

        item.addEventListener("click", () => {
          state.document = state.document === s.document_id ? "" : (s.document_id || "");
          this._notifyFilterChanged();
          this.closeAll();
        });
        listContainer.append(item);
      });
    }

    /* ──── 4. 状态与答案下拉 ──── */
    _renderStatusDropdown() {
      const panel = this.statusCapsule.panel;
      panel.replaceChildren();
      panel.className = "eagle-dropdown-panel eagle-simple-menu show";

      const state = this.context.state;

      const titleReview = el("div", "eyebrow", "审核方式");
      titleReview.style.fontSize = "11px";
      titleReview.style.fontWeight = "700";
      titleReview.style.color = "#94a3b8";
      titleReview.style.padding = "4px 8px";
      panel.append(titleReview);

      const reviews = [
        { key: "", label: "全部方式" },
        { key: "human", label: "人工核对" },
        { key: "ai", label: "AI 审核" }
      ];
      reviews.forEach(r => {
        const item = el("div", `eagle-menu-item${(state.review || "") === r.key ? " selected" : ""}`);
        item.append(el("span", "", r.label));
        item.addEventListener("click", () => {
          state.review = r.key;
          this._notifyFilterChanged();
          this.closeAll();
        });
        panel.append(item);
      });

      const titleAnswer = el("div", "eyebrow", "原卷答案");
      titleAnswer.style.fontSize = "11px";
      titleAnswer.style.fontWeight = "700";
      titleAnswer.style.color = "#94a3b8";
      titleAnswer.style.padding = "4px 8px";
      titleAnswer.style.marginTop = "8px";
      panel.append(titleAnswer);

      const answers = [
        { key: "", label: "全部" },
        { key: "yes", label: "有原卷答案" },
        { key: "no", label: "无原卷答案" }
      ];
      answers.forEach(a => {
        const item = el("div", `eagle-menu-item${(state.answer || "") === a.key ? " selected" : ""}`);
        item.append(el("span", "", a.label));
        item.addEventListener("click", () => {
          state.answer = a.key;
          this._notifyFilterChanged();
          this.closeAll();
        });
        panel.append(item);
      });
    }

    /* ──── 5. 排序下拉 ──── */
    _renderSortDropdown() {
      const panel = this.sortCapsule.panel;
      panel.replaceChildren();
      panel.className = "eagle-dropdown-panel eagle-simple-menu show";

      const state = this.context.state;
      const sorts = [
        { key: "recent", label: "最近入库" },
        { key: "source", label: "原卷顺序" },
        { key: "number", label: "按题号" }
      ];

      sorts.forEach(s => {
        const item = el("div", `eagle-menu-item${(state.sort || "recent") === s.key ? " selected" : ""}`);
        item.append(el("span", "", s.label));
        item.addEventListener("click", () => {
          state.sort = s.key;
          this._notifyFilterChanged();
          this.closeAll();
        });
        panel.append(item);
      });
    }

    /* ──── 6. 智能文件夹下拉 ──── */
    _renderSmartFolderDropdown() {
      const panel = this.smartFolderCapsule.panel;
      panel.replaceChildren();
      panel.className = "eagle-dropdown-panel eagle-smart-folder-panel show";

      const folders = getSmartFolders();

      const saveBtn = el("button", "button button-small", "★ 将当前筛选存为智能文件夹");
      saveBtn.type = "button";
      saveBtn.style.width = "100%";
      saveBtn.style.marginBottom = "6px";
      saveBtn.style.background = "#ecfdf5";
      saveBtn.style.color = "#047857";
      saveBtn.style.border = "1px solid #a7f3d0";
      saveBtn.style.fontWeight = "600";

      saveBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const defaultName = this._generateDefaultFolderName();
        const name = prompt("请输入智能文件夹名称：", defaultName);
        if (name && name.trim()) {
          const filterState = {
            q: this.context.state.q,
            type: this.context.state.type,
            document: this.context.state.document,
            review: this.context.state.review,
            answer: this.context.state.answer,
            sort: this.context.state.sort,
            tagMode: this.context.state.tagMode,
            tagAll: this.context.state.tagAll,
            tagAny: this.context.state.tagAny,
            tagNot: this.context.state.tagNot
          };
          if (saveSmartFolder(name, filterState)) {
            safeToast(`已保存智能文件夹「${name.trim()}」`, "success");
            this._renderSmartFolderDropdown();
            this._enhanceSidebar();
          }
        }
      });
      panel.append(saveBtn);

      const listContainer = el("div", "eagle-smart-folder-list");
      if (!folders.length) {
        listContainer.append(el("div", "eagle-menu-item", "暂无保存的智能文件夹"));
      } else {
        folders.forEach(folder => {
          const item = el("div", "eagle-folder-item");
          const label = el("span", "eagle-tag-name", "📁 " + folder.name);
          const delBtn = el("button", "eagle-folder-del-btn", "×");
          delBtn.title = "删除此智能文件夹";
          delBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (confirm(`确定删除智能文件夹「${folder.name}」吗？`)) {
              deleteSmartFolder(folder.id);
              this._renderSmartFolderDropdown();
              this._enhanceSidebar();
            }
          });

          item.append(label, delBtn);
          item.addEventListener("click", () => {
            this._applySmartFolder(folder);
            this.closeAll();
          });
          listContainer.append(item);
        });
      }
      panel.append(listContainer);
    }

    _generateDefaultFolderName() {
      const state = this.context.state;
      const parts = [];
      if (state.type) parts.push(TYPE_NAMES[state.type] || state.type);
      const selected = this._getSelectedTagIds();
      if (selected.length) parts.push(`${selected.length}个标签`);
      if (state.q) parts.push(`"${state.q}"`);
      return parts.join(" + ") || "自定义试题集";
    }

    _applySmartFolder(folder) {
      const state = this.context.state;
      const f = folder.filter || {};
      state.q = f.q || "";
      state.type = f.type || "";
      state.document = f.document || "";
      state.review = f.review || "";
      state.answer = f.answer || "";
      state.sort = f.sort || "recent";
      state.tagMode = f.tagMode || "all";
      state.tagAll = f.tagAll || "";
      state.tagAny = f.tagAny || "";
      state.tagNot = f.tagNot || "";
      state.tag = "";
      state.untaggedKnowledge = false;
      state.tagReview = Boolean(f.tagReview);

      const searchInput = document.getElementById("searchInput");
      if (searchInput) searchInput.value = state.q;

      safeToast(`已切入智能文件夹「${folder.name}」`, "success");
      this._notifyFilterChanged();
    }

    /* ──── 胶囊文字、高亮与内联 Active Chips 同步 ──── */
    syncUI() {
      if (!this.container || !this.context) return;
      const state = this.context.state;

      // 1. 题型
      if (state.type) {
        this.typeCapsule.btn.classList.add("is-active");
        this.typeCapsule.labelSpan.textContent = `题型: ${TYPE_NAMES[state.type] || state.type}`;
      } else {
        this.typeCapsule.btn.classList.remove("is-active");
        this.typeCapsule.labelSpan.textContent = "题型: 全部";
      }

      // 2. 标签
      const selectedTags = this._getSelectedTagIds();
      if (selectedTags.length > 0) {
        this.tagCapsule.btn.classList.add("is-active");
        const modeLabel = state.tagMode === "not" ? "NOT" : state.tagMode === "any" ? "OR" : "AND";
        this.tagCapsule.labelSpan.textContent = `标签: ${selectedTags.length}项 · ${modeLabel}`;
      } else {
        this.tagCapsule.btn.classList.remove("is-active");
        this.tagCapsule.labelSpan.textContent = "标签: 全部";
      }

      // 3. 来源试卷
      if (state.document) {
        this.sourceCapsule.btn.classList.add("is-active");
        const found = (state.facets?.sources || []).find(s => s.document_id === state.document);
        const name = found ? found.filename : "当前试卷";
        this.sourceCapsule.labelSpan.textContent = `来源: ${name.slice(0, 10)}${name.length > 10 ? "…" : ""}`;
      } else {
        this.sourceCapsule.btn.classList.remove("is-active");
        this.sourceCapsule.labelSpan.textContent = "来源: 全部试卷";
      }

      // 4. 状态与审核
      if (state.review || state.answer) {
        this.statusCapsule.btn.classList.add("is-active");
        const parts = [];
        if (state.review) parts.push(state.review === "ai" ? "AI审核" : "人工");
        if (state.answer) parts.push(state.answer === "yes" ? "有答案" : "无答案");
        this.statusCapsule.labelSpan.textContent = `状态: ${parts.join("/")}`;
      } else {
        this.statusCapsule.btn.classList.remove("is-active");
        this.statusCapsule.labelSpan.textContent = "状态: 全部";
      }

      // 5. 排序
      const sortNames = { recent: "最近入库", source: "原卷顺序", number: "按题号" };
      this.sortCapsule.labelSpan.textContent = `排序: ${sortNames[state.sort] || "最近入库"}`;
      if (state.sort && state.sort !== "recent") {
        this.sortCapsule.btn.classList.add("is-active");
      } else {
        this.sortCapsule.btn.classList.remove("is-active");
      }

      // 6. 渲染内联 Active Chips
      this._renderInlineChips();

      // 7. 增强卡片交互（原地展开解析等）
      this._bindCardEnhancements();
    }

    _renderInlineChips() {
      if (!this.inlineChipsContainer) return;
      this.inlineChipsContainer.replaceChildren();

      const state = this.context.state;
      const allTags = this._getAllTags();
      const chips = [];

      if (state.q && state.q.trim()) {
        chips.push({ key: "q", label: `"${state.q.trim()}"`, onRemove: () => { state.q = ""; const i = document.getElementById("searchInput"); if (i) i.value = ""; } });
      }

      if (state.type) {
        chips.push({ key: "type", label: TYPE_NAMES[state.type] || state.type, onRemove: () => { state.type = ""; } });
      }

      if (state.document) {
        const found = (state.facets?.sources || []).find(s => s.document_id === state.document);
        chips.push({ key: "doc", label: found ? found.filename : "当前卷", onRemove: () => { state.document = ""; } });
      }

      const selectedTagIds = this._getSelectedTagIds();
      selectedTagIds.forEach(id => {
        const t = allTags.find(x => String(x.id) === String(id));
        const name = t ? t.name : id;
        chips.push({
          key: "tag_" + id,
          label: name,
          isTag: true,
          onRemove: () => {
            this._toggleTagSelection(id);
          }
        });
      });

      if (!chips.length) {
        this.inlineChipsContainer.style.display = "none";
        return;
      }

      this.inlineChipsContainer.style.display = "inline-flex";

      chips.forEach(c => {
        const chipEl = el("div", `eagle-chip${c.isTag ? " chip-accent" : ""}`);
        chipEl.append(el("span", "", c.label));
        const closeBtn = el("button", "eagle-chip-close", "×");
        closeBtn.type = "button";
        closeBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          c.onRemove();
          this._notifyFilterChanged();
        });
        chipEl.append(closeBtn);
        this.inlineChipsContainer.append(chipEl);
      });

      // 清除全部小按钮
      const clearAll = el("button", "eagle-clear-all-btn", "清空");
      clearAll.type = "button";
      clearAll.addEventListener("click", (e) => {
        e.stopPropagation();
        state.q = "";
        state.type = "";
        state.document = "";
        state.review = "";
        state.answer = "";
        state.tagAll = "";
        state.tagAny = "";
        state.tagNot = "";
        state.tag = "";
        state.untaggedKnowledge = false;
        state.tagReview = false;
        const i = document.getElementById("searchInput");
        if (i) i.value = "";
        this._notifyFilterChanged();
      });
      this.inlineChipsContainer.append(clearAll);
    }

    _bindCardEnhancements() {
      // 为所有题目卡片赋予原地「查看解析」能力
      const cards = document.querySelectorAll(".library-card");
      cards.forEach(card => {
        if (card.dataset.eagleEnhanced === "1") return;
        card.dataset.eagleEnhanced = "1";

        const actions = card.querySelector(".library-card-actions");
        if (actions) {
          const solutionBtn = el("button", "button button-quiet button-small", "👁 查看解析");
          solutionBtn.type = "button";
          solutionBtn.style.color = "#047857";
          solutionBtn.style.fontWeight = "600";
          solutionBtn.style.borderRadius = "6px";

          let solutionBox = null;
          solutionBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (solutionBox) {
              solutionBox.remove();
              solutionBox = null;
              solutionBtn.textContent = "👁 查看解析";
              return;
            }

            solutionBox = el("div", "eagle-inline-solution-box");
            const pubId = card.id ? card.id.replace("q-", "") : "";
            solutionBox.innerHTML = '<div style="font-weight:bold;margin-bottom:6px;color:#0f172a;">【详细解析与解题思路】</div><div class="eagle-sol-content" style="color:#475569;">解析加载中…</div>';
            card.append(solutionBox);
            solutionBtn.textContent = "✕ 收起解析";

            // 请求并渲染解析
            if (pubId) {
              fetch(`/api/library/${pubId}/solution`, { headers: { 'X-QB-Request': '1', 'Referer': window.location.href } })
                .then(r => r.json())
                .then(res => {
                  const content = extractSolutionText(res);
                  const solEl = solutionBox.querySelector(".eagle-sol-content");
                  if (solEl) {
                    if (content) {
                      // 解析正文是纯文本（老师写的 / AI 生成的）：当文本塞进去，
                      // 既不让 <> 变成标签，也不让整段解析变成可执行内容。
                      solEl.textContent = content;
                      solEl.style.whiteSpace = 'pre-wrap';
                      solEl.style.color = '#475569';
                      solEl.style.fontStyle = '';
                      if (window.renderMathInElement) {
                        window.renderMathInElement(solEl, {
                          delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                          ],
                          throwOnError: false
                        });
                      }
                    } else {
                      solEl.textContent = '暂无原卷解析。可在题目详情中让 AI 辅助生成解答。';
                      solEl.style.color = '#94a3b8';
                      solEl.style.fontStyle = 'italic';
                    }
                  }
                })
                .catch(() => {
                  const solEl = solutionBox.querySelector(".eagle-sol-content");
                  if (solEl) solEl.textContent = "未能读取到原卷解析。";
                });
            }
          });
          actions.prepend(solutionBtn);
        }
      });
    }

    // 卡片是分批渲染的（首屏一批，滚动加载一批一批来）。
    // 以前只在筛选刷新时绑一次「👁 查看解析」，
    // 于是首屏那批卡片没有这个按钮，滚动加载的新卡片也没有 ——
    // 功能在，但用户点不到。这里盯着列表容器，一有新卡片就补上。
    // _bindCardEnhancements 本身是幂等的（data-eagle-enhanced 标记），
    // 所以自己加的按钮不会再触发新一轮绑定。
    _observeCardList() {
      if (this._cardObserver || typeof MutationObserver !== "function") return;
      const list = document.getElementById("libraryList");
      if (!list) return;
      this._cardObserver = new MutationObserver(() => {
        if (this._cardBindTimer) clearTimeout(this._cardBindTimer);
        this._cardBindTimer = setTimeout(() => {
          this._cardBindTimer = null;
          this._bindCardEnhancements();
        }, 60);
      });
      this._cardObserver.observe(list, { childList: true, subtree: true });
    }

    _notifyFilterChanged() {
      this.syncUI();
      if (this.context.onApplyFilter) {
        this.context.onApplyFilter();
      }
    }
  }

  window.EagleFilterBar = EagleFilterBar;
  window.EagleFilterBarInstance = new EagleFilterBar();
})();
