"use strict";
// Eagle 文件夹交互回归验证：
//   1. 点击右侧文件夹胶囊时：自动展开左侧栏（若已收起）、清掉冲突搜索、展开祖先并把目标节点滚动到左侧栏视野中央；
//   2. 打开「放入文件夹」弹窗时：不仅显示最外层文件夹，完整展示用户嵌套子文件夹与教材内部章节/小节文件夹，并支持搜索、展开与直接放入具体章节/小节。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function fnBody(name) {
  const lines = html.split(/\r?\n/);
  const start = lines.findIndex((l) =>
    new RegExp(`^\\s*(async )?function ${name}\\(`).test(l)
  );
  assert.ok(start >= 0, `应能找到函数 ${name}`);
  const indent = lines[start].match(/^\s*/)[0].length;
  let end = -1;
  for (let i = start + 1; i < lines.length; i += 1) {
    if (new RegExp(`^\\s{${indent}}\\}\\s*$`).test(lines[i])) {
      end = i;
      break;
    }
  }
  assert.ok(end > start, `应能找到函数 ${name} 的结束`);
  return lines.slice(start, end + 1).join("\n");
}

function createHarness(initialState = {}) {
  const store = new Map();
  const localStorage = {
    getItem(k) {
      return store.has(k) ? store.get(k) : null;
    },
    setItem(k, v) {
      store.set(k, String(v));
    },
    removeItem(k) {
      store.delete(k);
    },
  };
  if (initialState.userFolders) {
    localStorage.setItem("eagle_user_folders_v1", JSON.stringify(initialState.userFolders));
  }
  if (initialState.docOverrides) {
    localStorage.setItem("eagle_doc_folder_overrides_v1", JSON.stringify(initialState.docOverrides));
  }

  const scrollCalls = [];
  let nodeElements = new Map();

  function makeClassList(initial = []) {
    const set = new Set(initial);
    return {
      add(...cls) { cls.forEach((c) => set.add(c)); },
      remove(...cls) { cls.forEach((c) => set.delete(c)); },
      toggle(c) { if (set.has(c)) { set.delete(c); return false; } set.add(c); return true; },
      contains(c) { return set.has(c); },
    };
  }

  const leftFolderSidebar = { classList: makeClassList(initialState.sidebarCollapsed ? ["collapsed"] : []) };
  const treeSearchInput = { value: initialState.treeSearch || "" };
  const folderPickerModal = { classList: makeClassList(["hidden"]) };
  const folderPickerTitle = { innerText: "" };
  const folderPickerSubtitle = { innerText: "" };
  const folderPickerNewBtn = { classList: makeClassList([]) };
  const folderPickerSearchInput = { value: "" };
  const folderPickerTreeList = { innerHTML: "" };

  const folderTreeContainer = {
    innerHTML: "",
    scrollTop: 0,
    getBoundingClientRect() {
      return { top: 0, height: 400 };
    },
    scrollTo(opts) {
      scrollCalls.push(opts);
      if (opts && typeof opts.top === "number") {
        folderTreeContainer.scrollTop = opts.top;
      }
    },
  };

  function rebuildNodeElements() {
    nodeElements = new Map();
    const matches = [...folderTreeContainer.innerHTML.matchAll(/id="ftree-([^"]+)"/g)];
    matches.forEach((m, idx) => {
      const nodeId = m[1];
      const topPos = 80 + idx * 32;
      nodeElements.set("ftree-" + nodeId, {
        id: "ftree-" + nodeId,
        classList: makeClassList([]),
        getBoundingClientRect() {
          return { top: topPos - folderTreeContainer.scrollTop, height: 28 };
        },
        scrollIntoView(opts) {
          scrollCalls.push({ scrollIntoView: true, nodeId, ...opts });
        },
      });
    });
  }

  const context = {
    Map,
    Set,
    Array,
    Object,
    Number,
    String,
    Boolean,
    Math,
    Date,
    JSON,
    RegExp,
    localStorage,
    console,
    setTimeout,
    clearTimeout,
    window: { innerWidth: 1280, innerHeight: 800 },
    document: {
      getElementById(id) {
        if (id === "leftFolderSidebar") return leftFolderSidebar;
        if (id === "treeSearchInput") return treeSearchInput;
        if (id === "folderTreeContainer") return folderTreeContainer;
        if (id === "folderPickerModal") return folderPickerModal;
        if (id === "folderPickerTitle") return folderPickerTitle;
        if (id === "folderPickerSubtitle") return folderPickerSubtitle;
        if (id === "folderPickerNewBtn") return folderPickerNewBtn;
        if (id === "folderPickerSearchInput") return folderPickerSearchInput;
        if (id === "folderPickerTreeList") return folderPickerTreeList;
        return nodeElements.get(id) || null;
      },
    },
    escapeHtml(v) {
      return String(v == null ? "" : v)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    },
    escapeInlineJsArg(v) {
      return String(v || "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
    },
    getHeartIconSvg() { return "♥"; },
    updateEagleAppMainMenuCounts() {},
    sortAndRenderPapers() {},
    hideEagleContextMenu() {},
    showToast() {},
    renderInlineActiveChips() {},
    updateFavoritesBadge() {},
    applyFilters() { context._applyFiltersCount = (context._applyFiltersCount || 0) + 1; },
    renderRightInspector() { context._renderRightInspectorCount = (context._renderRightInspectorCount || 0) + 1; },
    toggleLeftSidebar() {
      leftFolderSidebar.classList.toggle("collapsed");
    },
    writeExpandedFolderNodes() {},
    readExpandedFolderNodes() {
      return new Set(initialState.expanded || ["section:folders", "group:books", "group:papers"]);
    },
    readRecentQuestions() { return []; },
    countQuestionsForFilterSpec() { return 0; },
    countQuestionsForTagIdSet() { return 0; },
    buildTagTreeNodes() { return []; },
    buildCustomSmartFolderTreeNodes() { return []; },
    readCustomSmartFolders() { return []; },
    getFolderParentDepth() { return 1; },
    canPlaceFolderUnderParent() { return true; },
    collectFolderSubtreeIds(list, rootId) { return new Set([rootId]); },
    scheduleFoldersSync() {},
    MAX_FOLDER_DEPTH: 4,
    USER_FOLDERS_STORAGE_KEY: "eagle_user_folders_v1",
    EAGLE_DOC_FOLDER_OVERRIDES_KEY: "eagle_doc_folder_overrides_v1",
    CUSTOM_SMART_FOLDERS_STORAGE_KEY: "eagle_custom_smart_folders",
    EAGLE_LAST_FOLDER_KEY: "eagle_last_folder_v1",
    papersData: initialState.papersData || [],
    questionsData: initialState.questionsData || [],
    tagCatalogue: [],
    favoritesSet: new Set(),
    folderTreeState: {
      expanded: new Set(initialState.expanded || ["section:folders", "group:books", "group:papers"]),
      activeId: "all",
      search: initialState.treeSearch || "",
    },
    folderNodeRegistry: new Map(),
    folderParentRegistry: new Map(),
    textbookChapterOf: new Map(),
    textbookSectionOf: new Map(),
    filterState: {
      keyword: "",
      paperId: "",
      paperName: "",
      userFolderId: "",
      folderType: "all",
      folderPaperIds: [],
      folderTagIds: [],
      folderSections: [],
      smartFilter: "",
    },
  };

  const snippets = [
    `let currentPaperSort = 'time_desc';`,
    `let folderPickerState = null;`,
    `const FOLDER_SORT_MODES_STORAGE_KEY = 'folder_sort_modes_v1';`,
    `const VALID_FOLDER_SORT_MODES = new Set(['time_desc', 'time_asc', 'name', 'count_desc']);`,
    `const ZH_NUM_DIGITS = {'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};`,
    `const ZH_NUM_UNITS = {'十':10,'百':100,'千':1000};`,
    `const PEP_A_COMPULSORY_1_CHAPTERS = {};`,
    `const PEP_A_COMPULSORY_1_SECTIONS = [];`,
    fnBody("readFolderSortModes"),
    fnBody("writeFolderSortModes"),
    `let folderSortModes = readFolderSortModes();`,
    fnBody("getExplicitFolderSortMode"),
    fnBody("resolveFolderSortMode"),
    fnBody("parseChineseNumeral"),
    fnBody("normalizeLabelForNaturalSort"),
    fnBody("compareNaturalZhLabels"),
    fnBody("folderCreatedAtMs"),
    fnBody("formatFolderCreatedLabel"),
    fnBody("compareFolderTreeNodes"),
    fnBody("sortFolderTreeNodes"),
    fnBody("folderIconFor"),
    fnBody("isSystemFolderParentId"),
    fnBody("isActiveSystemFolderParentId"),
    fnBody("readUserFolders"),
    fnBody("writeUserFolders"),
    fnBody("readDocFolderOverrides"),
    fnBody("writeDocFolderOverrides"),
    fnBody("isQuestionInDocFolder"),
    fnBody("collectUserFolderQuestionIds"),
    fnBody("collectMountedSubfolderQuestionIds"),
    fnBody("collectDocFolderQuestionIds"),
    fnBody("removeQuestionsFromDocFolder"),
    fnBody("addQuestionsToFolderTarget"),
    fnBody("buildUserFolderChildMap"),
    fnBody("buildUserFolderTreeNodesForParent"),
    fnBody("buildUserFolderTreeNodes"),
    fnBody("strongChapterAnchor"),
    fnBody("textbookGroupKey"),
    fnBody("parseBookSectionParts"),
    fnBody("isCompoundBookQuestions"),
    fnBody("isPepACompulsory1Book"),
    fnBody("textbookChapterTitleOf"),
    fnBody("chapterOfQuestion"),
    fnBody("sectionOfQuestion"),
    fnBody("assignTextbookChapters"),
    fnBody("parseTextbookTitle"),
    fnBody("buildTextbookTreeNodes"),
    fnBody("folderSectionMatchesForSpec"),
    fnBody("resolveQuestionTextbookLeafInfo"),
    fnBody("buildPaperTreeNodes"),
    fnBody("getFolderTreeRoots"),
    fnBody("collectTagIds"),
    fnBody("renderFolderTreeNode"),
    `function renderFolderTree() {
      const container = document.getElementById('folderTreeContainer');
      if (!container) return;
      folderNodeRegistry.clear();
      folderParentRegistry.clear();
      const roots = getFolderTreeRoots();
      const html = roots.map(r => renderFolderTreeNode(r, 0, null)).filter(Boolean).join('');
      container.innerHTML = html || '';
    }`,
    fnBody("expandFolderAncestors"),
    fnBody("revealFolderNodeInLeftTree"),
    fnBody("navigateToInspectorFolderNode"),
    fnBody("navigateToInspectorBookTarget"),
    fnBody("locateQuestionFolderInTree"),
    fnBody("applyFolderNode"),
    `let folderPickerExpanded = new Set();`,
    `let folderPickerSearch = '';`,
    `const folderPickerNodeMeta = new Map();`,
    fnBody("normalizePickerParentId"),
    fnBody("togglePickerNodeExpand"),
    fnBody("handleFolderPickerSearch"),
    fnBody("initFolderPickerExpandedState"),
    fnBody("openFolderPickerForQuestions"),
    fnBody("openFolderPickerForMove"),
    fnBody("buildPickerFolderRowsHtml"),
    fnBody("renderFolderPickerTreeList"),
    fnBody("selectFolderFromPicker"),
    fnBody("closeFolderPickerModal"),
  ];

  vm.runInNewContext(snippets.join("\n\n"), context);

  // Hook renderFolderTree so mocked DOM elements stay synced with generated HTML
  const origRender = context.renderFolderTree;
  context.renderFolderTree = function () {
    origRender();
    rebuildNodeElements();
  };

  return {
    context,
    leftFolderSidebar,
    treeSearchInput,
    folderTreeContainer,
    folderPickerModal,
    folderPickerTreeList,
    scrollCalls,
    getNodeEl: (id) => nodeElements.get("ftree-" + id),
  };
}

test("Eagle 文件夹定位居中与弹窗内部子文件夹完整展示", async (t) => {
  await t.test("1. 点击右侧文件夹胶囊：展开收起的左栏、清掉冲突搜索并把目标文件夹居中滚动到视野中央", () => {
    const h = createHarness({
      sidebarCollapsed: true,
      treeSearch: "不存在的过滤词",
      expanded: ["all"],
      userFolders: [
        { id: "uf_top", name: "总复习", parentId: null, questionIds: [] },
        { id: "uf_sub", name: "解析几何", parentId: "uf_top", questionIds: ["q1"] },
        { id: "uf_leaf", name: "椭圆专题", parentId: "uf_sub", questionIds: ["q1"] },
      ],
      papersData: [
        { id: "p1", name: "2026模拟卷", materialType: "exam", published: 1 },
      ],
      questionsData: [
        { id: "q1", documentId: "p1", section: "", number: 1 },
      ],
    });

    h.context.renderFolderTree();
    assert.equal(h.leftFolderSidebar.classList.contains("collapsed"), true);

    h.context.navigateToInspectorFolderNode("userfolder:uf_leaf");

    assert.equal(h.leftFolderSidebar.classList.contains("collapsed"), false, "应自动展开左侧栏");
    assert.equal(h.context.folderTreeState.search, "", "若搜索词屏蔽目标文件夹，应自动清除");
    assert.equal(h.treeSearchInput.value, "");
    assert.equal(h.context.folderTreeState.activeId, "userfolder:uf_leaf");
    assert.ok(h.getNodeEl("userfolder:uf_leaf"), "目标文件夹节点必须在左侧栏 DOM 中渲染出来");
    assert.ok(h.scrollCalls.length >= 1, "必须触发滚动定位");
    const lastScroll = h.scrollCalls[h.scrollCalls.length - 1];
    assert.ok(typeof lastScroll.top === "number" && lastScroll.top > 0, "应将下方目标文件夹滚动至视野中央");
    assert.equal(h.getNodeEl("userfolder:uf_leaf").classList.contains("ring-2"), true, "定位后应短暂高亮提示");
  });

  await t.test("2. 点击右侧教材小节胶囊：展开教材/章并定位到对应小节节点居中", () => {
    const h = createHarness({
      expanded: ["all"],
      papersData: [
        { id: "b1", name: "高中数学思想方法导引", materialType: "book", published: 2 },
      ],
      questionsData: [
        { id: "q1", documentId: "b1", section: "第1节 公式法 · 例题1", paperPage: 1, number: 1 },
        { id: "q2", documentId: "b1", section: "第2节 配方法 · 变式1", paperPage: 5, number: 2 },
      ],
    });

    h.context.renderFolderTree();
    const leaf = h.context.resolveQuestionTextbookLeafInfo(h.context.questionsData[1], h.context.papersData[0]);
    assert.equal(leaf.sectionName, "变式");

    h.context.navigateToInspectorBookTarget("b1", leaf.chapterKey, leaf.sectionName);
    assert.ok(h.context.folderTreeState.activeId.startsWith("booksec:b1:"), "应选中教材小节节点");
    assert.ok(h.getNodeEl(h.context.folderTreeState.activeId), "小节节点应在左侧树可见");
    assert.ok(h.scrollCalls.length >= 1, "应居中滚动左侧树");
  });

  await t.test("3. 「放入文件夹」弹窗完整展示用户多级子文件夹与教材内部章节/小节，并支持搜索与折叠切换", () => {
    const h = createHarness({
      userFolders: [
        { id: "uf_root", name: "自建父夹", parentId: null, questionIds: [] },
        { id: "uf_child", name: "内部子夹", parentId: "uf_root", questionIds: [] },
        { id: "uf_book_sub", name: "教材下自建夹", parentId: "book:b1", questionIds: [] },
      ],
      papersData: [
        { id: "p1", name: "期末测试卷", materialType: "exam", published: 1 },
        { id: "b1", name: "人教A版高中数学必修第一册", materialType: "book", published: 2 },
      ],
      questionsData: [
        { id: "q_exam", documentId: "p1", section: "", number: 1 },
        { id: "q_book_1", documentId: "b1", section: "1.1 集合的概念", paperPage: 2, number: 1 },
        { id: "q_book_2", documentId: "b1", section: "习题1.1", paperPage: 5, number: 2 },
      ],
    });

    h.context.openFolderPickerForQuestions(["q_exam"]);
    const htmlOut = h.folderPickerTreeList.innerHTML;
    assert.match(htmlOut, /自建父夹/, "应显示最外层自建文件夹");
    assert.match(htmlOut, /内部子夹/, "应显示自建文件夹内部的嵌套子文件夹");
    assert.match(htmlOut, /教材下自建夹/, "应显示挂在教材下的自建子文件夹");
    assert.match(htmlOut, /第1章/, "应显示教材内部的章节文件夹");
    assert.match(htmlOut, /1\.1 集合的概念/, "应显示教材章节内部的小节文件夹");

    // 折叠第1章后，小节应暂时收起；再次点击展开或搜索可恢复显示
    const chapMatch = htmlOut.match(/bookchap:b1:[^'"]+/);
    assert.ok(chapMatch, "应生成教材章节节点 ID");
    h.context.togglePickerNodeExpand(chapMatch[0]);
    assert.doesNotMatch(h.folderPickerTreeList.innerHTML, /1\.1 集合的概念/, "收起章节后其小节应折叠");

    h.context.handleFolderPickerSearch("集合");
    assert.match(h.folderPickerTreeList.innerHTML, /1\.1 集合的概念/, "搜索小节名时应自动展开并匹配内部小节");
  });

  await t.test("4. 在弹窗中选择教材内部小节放入跨卷试题后，左侧目录/筛选/右侧胶囊全部同步命中且移出时清理", () => {
    const h = createHarness({
      papersData: [
        { id: "p1", name: "期末测试卷", materialType: "exam", published: 1 },
        { id: "b1", name: "人教A版高中数学必修第一册", materialType: "book", published: 2 },
      ],
      questionsData: [
        { id: "q_exam", documentId: "p1", section: "选择题", number: 1 },
        { id: "q_book_1", documentId: "b1", section: "1.1 集合的概念", paperPage: 2, number: 1 },
        { id: "q_book_2", documentId: "b1", section: "习题1.1", paperPage: 5, number: 2 },
      ],
    });

    h.context.openFolderPickerForQuestions(["q_exam"]);
    const secMatch = h.folderPickerTreeList.innerHTML.match(/selectFolderFromPicker\('(booksec:b1:[^']+)'\)/);
    assert.ok(secMatch, "应能选中具体教材小节");
    const targetSecId = secMatch[1];

    h.context.selectFolderFromPicker(targetSecId);

    // 1. 左侧树该小节计数应包含新增题
    h.context.renderFolderTree();
    const secNode = h.context.folderNodeRegistry.get(targetSecId);
    assert.ok(secNode, "左侧目录树应保留该小节节点");
    assert.equal(secNode.count, 2, "放入后该小节题数应从 1 增至 2");

    // 2. 点击该小节筛选应命中跨卷放入的 q_exam
    h.context.applyFolderNode(targetSecId);
    const matched = h.context.folderSectionMatchesForSpec(h.context.questionsData[0], h.context.filterState);
    assert.equal(matched, true, "小节筛选必须命中跨卷放入该小节的试题");

    // 3. 右侧胶囊应显示具体小节路径，而不是只显示整本教材
    const leaf = h.context.resolveQuestionTextbookLeafInfo(h.context.questionsData[0], h.context.papersData[1]);
    assert.equal(leaf.sectionName, "1.1 集合的概念");
    assert.match(leaf.fullPath, /1\.1 集合的概念/);

    // 4. 从教材文件夹移出后清理章节覆盖
    h.context.removeQuestionsFromDocFolder(["q_exam"], "b1");
    const ov = h.context.readDocFolderOverrides().b1;
    assert.equal(Boolean(ov && ov.sections && ov.sections.q_exam), false, "移出后应一并清理 sections 映射");
  });
});
