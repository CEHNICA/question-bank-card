"use strict";
// Eagle 文件夹树排序与右键菜单覆盖验证：
//   1. 所有文件夹与子文件夹（自建夹、教材、章节、小节、标签、智能文件夹、分区与分组）右键均提供「排序方式」；
//   2. 按文件夹独立记录排序偏好（有子文件夹时排自身子文件夹；叶节点回退排所在父容器，全局为兜底）；
//   3. 中文序号 + 数字自然排序（第1讲 < 第2讲 < 第10讲；第一讲 < 第二讲 < 第十讲；1.2 < 1.10）；
//   4. 教材/教辅《高中数学思想方法导引》等小节不再死板按题量降序打乱，默认按书内页序/自然序排列。
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

function createSortHarness(initialState = {}) {
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
  if (initialState.paperSortMode) {
    localStorage.setItem("paper_sort_mode", initialState.paperSortMode);
  }
  if (initialState.userFolders) {
    localStorage.setItem("eagle_user_folders_v1", JSON.stringify(initialState.userFolders));
  }
  if (initialState.customSmartFolders) {
    localStorage.setItem("eagle_custom_smart_folders", JSON.stringify(initialState.customSmartFolders));
  }

  const menuEl = { innerHTML: "", classList: { add() {}, remove() {} }, style: {} };
  const folderTreeContainer = { innerHTML: "" };
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
    window: { innerWidth: 1280, innerHeight: 800 },
    document: {
      getElementById(id) {
        if (id === "eagleContextMenu") return menuEl;
        if (id === "folderTreeContainer") return folderTreeContainer;
        return null;
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
    getHeartIconSvg() {
      return "♥";
    },
    updateEagleAppMainMenuCounts() {},
    sortAndRenderPapers() {},
    positionEagleContextMenu() {},
    hideEagleContextMenu() {
      context.activeContextMenuPaperId = null;
      context.activeContextMenuSortTargetId = null;
    },
    getFolderParentDepth() {
      return 1;
    },
    treeTagHasOwnQuestions() {
      return true;
    },
    trashTotalCount() {
      return 0;
    },
    readRecentQuestions() {
      return [];
    },
    countQuestionsForFilterSpec() {
      return 0;
    },
    readDocFolderOverrides() {
      return {};
    },
    collectDocFolderQuestionIds() {
      return new Set();
    },
    collectUserFolderQuestionIds(fid, list) {
      const f = (list || []).find((x) => x.id === fid);
      return new Set((f && f.questionIds) || []);
    },
    isQuestionInDocFolder(q, docId) {
      return String(q.documentId) === String(docId);
    },
    MAX_FOLDER_DEPTH: 4,
    USER_FOLDERS_STORAGE_KEY: "eagle_user_folders_v1",
    CUSTOM_SMART_FOLDERS_STORAGE_KEY: "eagle_custom_smart_folders",
    papersData: initialState.papersData || [],
    questionsData: initialState.questionsData || [],
    tagCatalogue: initialState.tagCatalogue || [],
    favoritesSet: new Set(),
    folderTreeState: {
      expanded: new Set(initialState.expanded || ["section:folders", "section:tags", "section:custom", "group:books", "group:papers", "group:knowledge", "group:method"]),
      activeId: "all",
      search: "",
    },
    folderNodeRegistry: new Map(),
    folderParentRegistry: new Map(),
    textbookChapterOf: new Map(),
    textbookSectionOf: new Map(),
  };

  const snippets = [
    `let currentPaperSort = localStorage.getItem('paper_sort_mode') || 'time_desc';`,
    `const FOLDER_SORT_MODES_STORAGE_KEY = 'folder_sort_modes_v1';`,
    `const VALID_FOLDER_SORT_MODES = new Set(['time_desc', 'time_asc', 'name', 'count_desc', 'manual']);`,
    fnBody("readFolderSortModes"),
    fnBody("writeFolderSortModes"),
    `let folderSortModes = readFolderSortModes();`,
    fnBody("getExplicitFolderSortMode"),
    fnBody("resolveFolderSortMode"),
    `let activeContextMenuPaperId = null;`,
    `let activeContextMenuSortTargetId = null;`,
    `const ZH_NUM_DIGITS = {'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};`,
    `const ZH_NUM_UNITS = {'十':10,'百':100,'千':1000};`,
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
    `const FOLDER_SORT_ORDERS_MIRROR_KEY = 'eagle_folder_sort_orders_v1';`,
    fnBody("readFolderSortOrdersMirror"),
    fnBody("writeFolderSortOrdersMirror"),
    fnBody("updateFolderSortOrdersMirrorFromList"),
    fnBody("applyFolderSortOrdersMirror"),
    fnBody("readUserFolders"),
    fnBody("buildUserFolderChildMap"),
    fnBody("buildUserFolderTreeNodesForParent"),
    fnBody("buildUserFolderTreeNodes"),
    fnBody("readCustomSmartFolders"),
    fnBody("buildCustomSmartFolderTreeNodes"),
    fnBody("countQuestionsForTagIdSet"),
    fnBody("buildTagTreeNodes"),
    fnBody("strongChapterAnchor"),
    fnBody("textbookGroupKey"),
    fnBody("parseBookSectionParts"),
    fnBody("isCompoundBookQuestions"),
    `const PEP_A_COMPULSORY_1_CHAPTERS = {
      1: '第1章 集合与常用逻辑用语',
      2: '第2章 一元二次函数、方程和不等式',
      3: '第3章 函数的概念与性质',
      4: '第4章 指数函数与对数函数',
      5: '第5章 三角函数'
    };`,
    `const PEP_A_COMPULSORY_1_SECTIONS = [
      { ch: 1, lo: 1, hi: 13, label: '1.1 集合的概念' },
      { ch: 1, lo: 14, hi: 16, label: '1.2 集合间的基本关系' },
      { ch: 1, lo: 17, hi: 21, label: '1.3 集合的基本运算' },
      { ch: 1, lo: 22, hi: 30, label: '1.4 充分条件与必要条件' },
      { ch: 1, lo: 31, hi: 39, label: '1.5 全称量词与存在量词' },
      { ch: 1, lo: 40, hi: 43, label: '本章小结与复习参考题1' },
      { ch: 2, lo: 44, hi: 50, label: '2.1 等式性质与不等式性质' },
      { ch: 2, lo: 51, hi: 56, label: '2.2 基本不等式' },
      { ch: 2, lo: 57, hi: 62, label: '2.3 二次函数与一元二次方程、不等式' },
      { ch: 2, lo: 63, hi: 66, label: '本章小结与复习参考题2' },
      { ch: 3, lo: 67, hi: 81, label: '3.1 函数的概念及其表示' },
      { ch: 3, lo: 82, hi: 94, label: '3.2 函数的基本性质' },
      { ch: 3, lo: 95, hi: 99, label: '3.3 幂函数' },
      { ch: 3, lo: 100, hi: 105, label: '3.4 函数的应用（一）' },
      { ch: 3, lo: 106, hi: 110, label: '本章小结与复习参考题3' },
      { ch: 4, lo: 111, hi: 117, label: '4.1 指数' },
      { ch: 4, lo: 118, hi: 127, label: '4.2 指数函数' },
      { ch: 4, lo: 128, hi: 135, label: '4.3 对数' },
      { ch: 4, lo: 136, hi: 148, label: '4.4 对数函数' },
      { ch: 4, lo: 149, hi: 164, label: '4.5 函数的应用（二）' },
      { ch: 4, lo: 165, hi: 168, label: '本章小结与复习参考题4' },
      { ch: 4, lo: 169, hi: 173, label: '数学建模 建立函数模型解决实际问题' },
      { ch: 5, lo: 174, hi: 183, label: '5.1 任意角和弧度制' },
      { ch: 5, lo: 184, hi: 193, label: '5.2 三角函数的概念' },
      { ch: 5, lo: 194, hi: 202, label: '5.3 诱导公式' },
      { ch: 5, lo: 203, hi: 221, label: '5.4 三角函数的图象与性质' },
      { ch: 5, lo: 222, hi: 237, label: '5.5 三角恒等变换' },
      { ch: 5, lo: 238, hi: 249, label: '5.6 函数 y=Asin(ωx+φ)' },
      { ch: 5, lo: 250, hi: 257, label: '5.7 三角函数的应用' },
      { ch: 5, lo: 258, hi: 320, label: '本章小结与复习参考题5' }
    ];`,
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
    fnBody("renderFolderTreeNode"),
    fnBody("renderFolderTree"),
    fnBody("treeMenuHeader"),
    fnBody("treeMenuItem"),
    fnBody("treeMenuDivider"),
    fnBody("treeExpandCollapseLabels"),
    fnBody("treeSortModeOptions"),
    fnBody("isBookNaturalSortDefault"),
    fnBody("resolveContextSortTargetId"),
    fnBody("treeSortCurrentLabel"),
    fnBody("eagleMenuFlyout"),
    fnBody("treeSortMenuBlock"),
    fnBody("openEagleContextMenu"),
    fnBody("handleFolderTreeBlankContextMenu"),
    fnBody("handleFolderTreeContextMenu"),
    fnBody("setPaperSortMode"),
    fnBody("setFolderSortMode"),
    fnBody("clearFolderSortMode"),
  ];

  vm.runInNewContext(snippets.join("\n\n"), context);
  return { context, menuEl, folderTreeContainer };
}

test("Eagle 文件夹排序：全树右键入口、中文自然排序与导引目录秩序", async (t) => {
  await t.test("1. 中文序号与阿拉伯数字自然排序（含第一讲..第十讲、1.2 < 1.10）", () => {
    const { context } = createSortHarness();
    const sortLabels = (arr) =>
      arr.slice().sort((a, b) => context.compareNaturalZhLabels(a, b));

    assert.deepEqual(
      sortLabels(["第10讲 动点问题", "第2讲 数形结合", "第1讲 函数与方程"]),
      ["第1讲 函数与方程", "第2讲 数形结合", "第10讲 动点问题"]
    );
    assert.deepEqual(
      sortLabels(["第十讲 转化与化归", "第二讲 分类讨论", "第八讲 构造法", "第一讲 配方法", "第二十一讲 综合"]),
      ["第一讲 配方法", "第二讲 分类讨论", "第八讲 构造法", "第十讲 转化与化归", "第二十一讲 综合"]
    );
    assert.deepEqual(
      sortLabels(["1.10 小结", "1.2 集合间关系", "2.1 函数概念", "1.1 集合含义"]),
      ["1.1 集合含义", "1.2 集合间关系", "1.10 小结", "2.1 函数概念"]
    );
    assert.deepEqual(
      sortLabels(["专题十", "专题二", "专题一"]),
      ["专题一", "专题二", "专题十"]
    );
    assert.deepEqual(
      sortLabels(["下册", "上册", "中册"]),
      ["上册", "中册", "下册"]
    );
    assert.deepEqual(
      sortLabels(["（未标注小节）", "第二讲", "第一讲"]),
      ["第一讲", "第二讲", "（未标注小节）"]
    );
    // 非序号普通中文词组不被误拆成数字
    assert.equal(context.normalizeLabelForNaturalSort("一般问题"), "一般问题");
    assert.equal(context.normalizeLabelForNaturalSort("三角函数"), "三角函数");
  });

  await t.test("2. 所有文件夹与子文件夹右键均包含「排序方式」", () => {
    const { context, menuEl } = createSortHarness({
      papersData: [
        { id: "p1", name: "2025年测试卷", published: 5, total: 5, materialType: "exam", createdAt: new Date("2025-06-01") },
        { id: "b1", name: "高中数学必修一", published: 6, total: 6, materialType: "book", createdAt: new Date("2025-01-01") },
        { id: "b2", name: "高中数学思想方法导引", published: 4, total: 4, materialType: "book", createdAt: new Date("2025-02-01") },
      ],
      questionsData: [
        { id: "q1", documentId: "b1", section: "1.1 集合", paperPage: 12, number: 1 },
        { id: "q3", documentId: "b1", section: "习题1.1", paperPage: 20, number: 1 },
        { id: "q4", documentId: "b2", section: "第一讲 函数与方程", paperPage: 3, number: 1 },
        { id: "q5", documentId: "b2", section: "第二讲 数形结合", paperPage: 15, number: 1 },
      ],
      userFolders: [
        { id: "uf_root", name: "专题整理", parentId: null, questionIds: [], createdAt: 1000 },
        { id: "uf_child", name: "导数压轴", parentId: "uf_root", questionIds: ["q1"], createdAt: 2000 },
      ],
      customSmartFolders: [
        { id: "csf_1", name: "高频错题", parentId: null, filter: {}, createdAt: 1000 },
        { id: "csf_2", name: "解析几何", parentId: "csf_1", filter: {}, createdAt: 2000 },
      ],
      tagCatalogue: [
        { id: "t1", name: "函数", kind: "knowledge", parent_ids: [], active: true, count: 2 },
        { id: "t2", name: "单调性", kind: "knowledge", parent_ids: ["t1"], active: true, count: 1 },
      ],
    });
    context.renderFolderTree();

    const fakeEvt = { preventDefault() {}, stopPropagation() {}, clientX: 100, clientY: 100, target: null };
    const nodeIdsToCheck = [
      "section:folders",
      "section:tags",
      "section:custom",
      "group:papers",
      "group:books",
      "group:knowledge",
      "userfolder:uf_root",
      "userfolder:uf_child",
      "book:b1",
      "book:b2",
      "paper:p1",
      "bookchap:b1:1",
      "bookchap:b2:none",
      "tag:t1",
      "tag:t2",
      "custom:csf_1",
      "custom:csf_2",
    ];
    for (const id of nodeIdsToCheck) {
      const node = context.folderNodeRegistry.get(id);
      assert.ok(node, `树上应存在节点 ${id}`);
      if (node.children && node.children.length && node.type === "bookchapter") {
        // 一并检查小节叶节点
        nodeIdsToCheck.push(node.children[0].id);
      }
    }
    for (const id of nodeIdsToCheck) {
      menuEl.innerHTML = "";
      context.handleFolderTreeContextMenu(fakeEvt, id);
      assert.match(menuEl.innerHTML, /排序方式/, `节点 ${id} 右键菜单必须包含「排序方式」`);
    }

    // 空白处右键同样有「排序方式」
    menuEl.innerHTML = "";
    context.handleFolderTreeBlankContextMenu(fakeEvt);
    assert.match(menuEl.innerHTML, /排序方式/, "树空白处右键必须包含「排序方式」");
  });

  await t.test("3. 《高中数学思想方法导引》教材树默认按书内页序/自然序，不再按题量倒序打乱", () => {
    const { context } = createSortHarness({
      papersData: [
        { id: "guide", name: "高中数学思想方法导引", published: 9, total: 9, materialType: "book", createdAt: new Date("2025-05-01") },
      ],
      questionsData: [
        // 第十讲题量最多(5题)，以前会被 b[1]-a[1] 硬排到最前面
        { id: "g1", documentId: "guide", section: "第十讲 转化与化归", paperPage: 105, number: 1 },
        { id: "g2", documentId: "guide", section: "第十讲 转化与化归", paperPage: 106, number: 2 },
        { id: "g3", documentId: "guide", section: "第十讲 转化与化归", paperPage: 107, number: 3 },
        { id: "g4", documentId: "guide", section: "第十讲 转化与化归", paperPage: 108, number: 4 },
        { id: "g5", documentId: "guide", section: "第十讲 转化与化归", paperPage: 109, number: 5 },
        // 第一讲只有 1 题，但在第 2 页
        { id: "g6", documentId: "guide", section: "第一讲 函数与方程", paperPage: 2, number: 1 },
        // 第二讲有 2 题，在第 18 页
        { id: "g7", documentId: "guide", section: "第二讲 数形结合", paperPage: 18, number: 1 },
        { id: "g8", documentId: "guide", section: "第二讲 数形结合", paperPage: 19, number: 2 },
        // 未标注小节有 1 题，即使页码靠前也应放在最后
        { id: "g9", documentId: "guide", section: "", paperPage: 1, number: 1 },
      ],
    });
    context.renderFolderTree();

    const bookNode = context.folderNodeRegistry.get("book:guide");
    assert.ok(bookNode, "应生成导引书节点");
    const chapNode = bookNode.children.find((c) => c.type === "bookchapter");
    assert.ok(chapNode, "应生成章节节点");
    assert.deepEqual(
      Array.from(chapNode.children, (s) => s.label),
      ["第一讲 函数与方程", "第二讲 数形结合", "第十讲 转化与化归", "（未标注小节）"],
      "默认应按书内页码与自然序排列，不得按题量倒序把第十讲顶到前面"
    );

    // 右键该章节切换到「按包含试题数 (多到少)」后，只影响该章节自身
    context.setFolderSortMode(chapNode.id, "count_desc");
    const afterCountSort = Array.from(context.folderNodeRegistry.get(chapNode.id).children, (s) => s.label);
    assert.equal(afterCountSort[0], "第十讲 转化与化归", "切换按题量排序后应立即按题量重排");

    // 恢复默认章节序后，回到自然页序
    context.clearFolderSortMode(chapNode.id);
    const afterReset = Array.from(context.folderNodeRegistry.get(chapNode.id).children, (s) => s.label);
    assert.deepEqual(
      afterReset,
      ["第一讲 函数与方程", "第二讲 数形结合", "第十讲 转化与化归", "（未标注小节）"]
    );
  });

  await t.test("4. 按文件夹独立排序与叶节点回退父容器", () => {
    const { context } = createSortHarness({
      paperSortMode: "time_desc",
      papersData: [
        { id: "p_old", name: "A-旧试卷", published: 5, total: 5, materialType: "exam", createdAt: new Date("2025-01-01") },
        { id: "p_new", name: "Z-新试卷", published: 5, total: 5, materialType: "exam", createdAt: new Date("2025-12-01") },
      ],
      userFolders: [
        { id: "guide_dir", name: "高中数学思想方法导引", parentId: null, questionIds: [], createdAt: 1000 },
        { id: "lec_1", name: "第一讲", parentId: "guide_dir", questionIds: [], createdAt: 1001 },
        { id: "lec_10", name: "第十讲", parentId: "guide_dir", questionIds: [], createdAt: 1002 },
        { id: "lec_2", name: "第二讲", parentId: "guide_dir", questionIds: [], createdAt: 1003 },
      ],
    });
    context.renderFolderTree();

    // 默认全局 time_desc 下，试卷按新->旧，导引子文件夹也按创建新->旧
    assert.deepEqual(
      Array.from(context.folderNodeRegistry.get("group:papers").children, (x) => x.label),
      ["Z-新试卷", "A-旧试卷"]
    );

    // 在叶节点「第二讲」上解析排序目标 -> 应落到其父夹「高中数学思想方法导引」
    assert.equal(
      context.resolveContextSortTargetId("userfolder:lec_2"),
      "userfolder:guide_dir",
      "无子文件夹的叶节点右键排序应作用于其所在父文件夹"
    );
    // 有子文件夹的「高中数学思想方法导引」本身 -> 排它自己的子文件夹
    assert.equal(
      context.resolveContextSortTargetId("userfolder:guide_dir"),
      "userfolder:guide_dir",
      "有子文件夹的节点右键排序应排自身子文件夹"
    );

    // 把导引文件夹设为按名称升序 -> 子文件夹自然序排列，同时试卷仍保留新->旧
    context.setFolderSortMode("userfolder:guide_dir", "name");
    assert.deepEqual(
      Array.from(context.folderNodeRegistry.get("userfolder:guide_dir").children, (x) => x.label),
      ["第一讲", "第二讲", "第十讲"]
    );
    assert.deepEqual(
      Array.from(context.folderNodeRegistry.get("group:papers").children, (x) => x.label),
      ["Z-新试卷", "A-旧试卷"],
      "调整导引文件夹排序不得打乱「试卷」的创建时间排序"
    );
  });

  await t.test("5. 《高中数学思想方法导引》复合节名重建为「节 → 栏目」，彻底消除其他小节与单题碎片夹", () => {
    const doc = { id: "guide_real", name: "高中数学思想方法导引", published: 8, total: 8, materialType: "book", createdAt: new Date("2025-05-01") };
    const qs = [
      { id: "r1", documentId: "guide_real", section: "第10节 构造法 · 巩固练习1", paperPage: 36, number: 6 },
      { id: "r2", documentId: "guide_real", section: "第1节 公式法 · 例题1", paperPage: 2, number: 1 },
      { id: "r3", documentId: "guide_real", section: "第1节 公式法 · 例题2", paperPage: 2, number: 2 },
      { id: "r4", documentId: "guide_real", section: "第1节 公式法 · 巩固练习1", paperPage: 4, number: 6 },
      { id: "r5", documentId: "guide_real", section: "第1节 公式法 · 巩固练习2", paperPage: 4, number: 7 },
      { id: "r6", documentId: "guide_real", section: "第2节 配方法 · 例题1", paperPage: 5, number: 1 },
      { id: "r7", documentId: "guide_real", section: "第44节 合一变形 · 变式1", paperPage: 163, number: 3 },
      { id: "r8", documentId: "guide_real", section: "第72节 模型法 · 巩固练习5", paperPage: 275, number: 7 },
    ];
    const { context, folderTreeContainer } = createSortHarness({
      papersData: [doc],
      questionsData: qs,
    });
    context.renderFolderTree();

    const bookNode = context.folderNodeRegistry.get("book:guide_real");
    assert.ok(bookNode, "应生成导引书节点");
    assert.deepEqual(
      Array.from(bookNode.children, (c) => c.label),
      ["第1节 公式法", "第2节 配方法", "第10节 构造法", "第44节 合一变形", "第72节 模型法"],
      "书下第一级直接呈现按序排列的节文件夹，不再多套一层「其他小节」"
    );
    assert.ok(!bookNode.children.some((c) => c.label === "其他小节"));

    const sec1 = bookNode.children[0];
    assert.equal(sec1.count, 4);
    assert.deepEqual(
      Array.from(sec1.children, (s) => [s.label, s.count]),
      [["例题", 2], ["巩固练习", 2]],
      "节下应按「例题 / 巩固练习」聚合，不能炸出「例题1」「例题2」单题碎片夹"
    );

    // 点击节级筛选 & 栏目级筛选均精确命中
    const sec1Matched = qs.filter((q) =>
      context.folderSectionMatchesForSpec(q, {
        folderSections: [{ paperId: "guide_real", kind: "chapter", value: sec1.chapterNo, groupName: sec1.chapterGroupName }],
      })
    );
    assert.deepEqual(sec1Matched.map((q) => q.id), ["r2", "r3", "r4", "r5"]);

    const subExample = sec1.children[0];
    const subMatched = qs.filter((q) =>
      context.folderSectionMatchesForSpec(q, {
        folderSections: [{
          paperId: "guide_real",
          kind: "section",
          chapterNo: subExample.chapterNo,
          groupName: subExample.chapterGroupName,
          value: subExample.sectionName,
          matchMode: subExample.sectionMatchMode,
        }],
      })
    );
    assert.deepEqual(subMatched.map((q) => q.id), ["r2", "r3"]);

    // 右侧详情路径胶囊不再出现「其他小节」
    const leafInfo = context.resolveQuestionTextbookLeafInfo(qs[1], doc);
    assert.equal(leafInfo.fullPath, "教材版本 / 高中数学思想方法导引 / 第1节 公式法 / 例题");
    assert.equal(leafInfo.chapterKey, sec1.chapterKey);
    assert.equal(leafInfo.sectionName, "例题");

    // 树节点 HTML 不再叠放 style="padding-left:..." 双重缩进
    assert.doesNotMatch(folderTreeContainer.innerHTML, /style="padding-left:/);
  });

  await t.test("6. 人教A版《必修第一册》按标准教材目录（5章30节）重建，消除碎片与OCR噪声小节", () => {
    const doc = { id: "pep_a1", name: "（A版）必修 第一册.pdf", filename: "（A版）必修 第一册.pdf", published: 7, total: 7, materialType: "book", createdAt: new Date("2025-05-01") };
    const qs = [
      { id: "m1", documentId: "pep_a1", section: "思考", paperPage: 10, number: 1 },
      { id: "m2", documentId: "pep_a1", section: "习题 1.3", paperPage: 21, number: 1 },
      { id: "m3", documentId: "pep_a1", section: "2.1 等式性质与不等式性质", paperPage: 45, number: 1 },
      { id: "m4", documentId: "pep_a1", section: "3.1.2 函数的表示法", paperPage: 74, number: 4 },
      { id: "m5", documentId: "pep_a1", section: "2. 选择题", paperPage: 116, number: 1 },
      { id: "m6", documentId: "pep_a1", section: "1.178 097 245.", paperPage: 181, number: 5 },
      { id: "m7", documentId: "pep_a1", section: "1. 选择题", paperPage: 247, number: 1 },
    ];
    const { context } = createSortHarness({
      papersData: [doc],
      questionsData: qs,
    });
    context.renderFolderTree();

    const bookNode = context.folderNodeRegistry.get("book:pep_a1");
    assert.ok(bookNode, "应生成必修第一册节点");
    assert.equal(bookNode.label, "A版 · 必修第一册");
    assert.deepEqual(
      Array.from(bookNode.children, (c) => c.label),
      [
        "第1章 集合与常用逻辑用语",
        "第2章 一元二次函数、方程和不等式",
        "第3章 函数的概念与性质",
        "第4章 指数函数与对数函数",
        "第5章 三角函数",
      ],
      "应呈现带完整章名的 5 个章节文件夹"
    );

    const ch1 = bookNode.children[0];
    assert.deepEqual(
      Array.from(ch1.children, (s) => [s.label, s.count]),
      [
        ["1.1 集合的概念", 1],
        ["1.3 集合的基本运算", 1],
      ],
      "p.10 的「思考」应归入 1.1 集合的概念，p.21 的「习题 1.3」应归入 1.3 集合的基本运算"
    );

    // 验证原本跨章溢出的边界页码（p.45 -> 2.1, p.74 -> 3.1, p.181 -> 5.1, p.247 -> 5.6）
    assert.equal(bookNode.children[1].children[0].label, "2.1 等式性质与不等式性质");
    assert.equal(bookNode.children[2].children[0].label, "3.1 函数的概念及其表示");
    assert.equal(bookNode.children[3].children[0].label, "4.1 指数");
    assert.deepEqual(
      Array.from(bookNode.children[4].children, (s) => s.label),
      ["5.1 任意角和弧度制", "5.6 函数 y=Asin(ωx+φ)"]
    );

    // 点击小节筛选精确命中
    const sec51 = bookNode.children[4].children[0];
    const matched51 = qs.filter((q) =>
      context.folderSectionMatchesForSpec(q, {
        folderSections: [{
          paperId: "pep_a1",
          kind: "section",
          chapterNo: sec51.chapterNo,
          value: sec51.sectionName,
          matchMode: sec51.sectionMatchMode,
        }],
      })
    );
    assert.deepEqual(matched51.map((q) => q.id), ["m6"]);

    // 右侧详情胶囊显示标准章名与标准节名
    const leafInfo = context.resolveQuestionTextbookLeafInfo(qs[5], doc);
    assert.equal(leafInfo.fullPath, "教材版本 / A版 · 必修第一册 / 第5章 三角函数 / 5.1 任意角和弧度制");
    assert.equal(leafInfo.leafLabel, "5.1 任意角和弧度制");
  });
});
