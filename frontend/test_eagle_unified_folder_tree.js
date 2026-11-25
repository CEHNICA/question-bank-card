"use strict";
// Eagle 大一统数据驱动文件夹树测试：
//   1. 8 层深度上限（8 层允许，第 9 层拒绝）
//   2. custom_tree_docs 启用后彻底跳过人教A版必修一硬编码表与「 · 」拆两层，直接按数据树渲染多级目录
//   3. doc_placements 允许将书本/试卷移入自建夹或根目录，父夹自动汇总题数，且可拖拽改位置/同级拖排并 Ctrl+Z 撤销
//   4. applyServerFoldersState 同步持久 doc_placements 与 custom_tree_docs
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

function createUnifiedTreeHarness(initialState = {}) {
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
  if (initialState.docPlacements) {
    localStorage.setItem("eagle_doc_placements_v1", JSON.stringify(initialState.docPlacements));
  }
  if (initialState.customTreeDocs) {
    localStorage.setItem("eagle_custom_tree_docs_v1", JSON.stringify(initialState.customTreeDocs));
  }
  if (initialState.sortModes) {
    localStorage.setItem("folder_sort_modes_v1", JSON.stringify(initialState.sortModes));
  }

  const folderTreeContainer = { innerHTML: "" };
  const toasts = [];
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
        if (id === "folderTreeContainer") return folderTreeContainer;
        return null;
      },
      querySelectorAll() {
        return [];
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
    renderRightInspector() {},
    renderInlineActiveChips() {},
    applyFilters() {},
    hideEagleContextMenu() {},
    showToast(msg, kind) {
      toasts.push({ msg, kind });
    },
    scheduleFoldersSync() {},
    writeExpandedFolderNodes() {},
    readRecentQuestions() {
      return [];
    },
    countQuestionsForFilterSpec() {
      return 0;
    },
    countQuestionsForTagIdSet() {
      return 0;
    },
    buildTagTreeNodes() {
      return [];
    },
    readCustomSmartFolders() {
      return [];
    },
    buildCustomSmartFolderTreeNodes() {
      return [];
    },
    USER_FOLDERS_STORAGE_KEY: "eagle_user_folders_v1",
    EAGLE_DOC_FOLDER_OVERRIDES_KEY: "eagle_doc_folder_overrides_v1",
    EAGLE_DOC_PLACEMENTS_KEY: "eagle_doc_placements_v1",
    EAGLE_CUSTOM_TREE_DOCS_KEY: "eagle_custom_tree_docs_v1",
    CUSTOM_SMART_FOLDERS_STORAGE_KEY: "eagle_custom_smart_folders",
    FOLDER_SORT_MODES_STORAGE_KEY: "folder_sort_modes_v1",
    EAGLE_TRASH_STORAGE_KEY: "eagle_trash_v1",
    EAGLE_LAST_FOLDER_KEY: "eagle_last_folder_v1",
    VALID_FOLDER_SORT_MODES: new Set(["time_desc", "time_asc", "name", "count_desc", "manual"]),
    FOLDER_UNDO_LIMIT: 40,
    folderOrganizeUndoStack: [],
    folderOrganizeRedoStack: [],
    _suppressFolderUndoCapture: false,
    _suppressFoldersSync: false,
    libraryFoldersRevision: 0,
    papersData: initialState.papersData || [],
    questionsData: initialState.questionsData || [],
    tagCatalogue: [],
    favoritesSet: new Set(),
    folderTreeState: {
      expanded: new Set(["section:folders", "group:books", "group:papers"]),
      activeId: "all",
      search: "",
    },
    folderNodeRegistry: new Map(),
    folderParentRegistry: new Map(),
    textbookChapterOf: new Map(),
    textbookSectionOf: new Map(),
    filterState: { userFolderId: "", smartFilter: "", folderPaperIds: [], paperId: "" },
    toasts,
  };

  const snippets = [
    `let currentPaperSort = 'time_desc';`,
    `const VALID_FOLDER_SORT_MODES = new Set(['time_desc', 'time_asc', 'name', 'count_desc', 'manual']);`,
    `const FOLDER_SORT_ORDERS_MIRROR_KEY = 'eagle_folder_sort_orders_v1';`,
    fnBody("readFolderSortModes"),
    fnBody("writeFolderSortModes"),
    `let folderSortModes = readFolderSortModes();`,
    fnBody("getExplicitFolderSortMode"),
    fnBody("resolveFolderSortMode"),
    fnBody("setPaperSortMode"),
    fnBody("setFolderSortMode"),
    fnBody("applyServerFoldersState"),
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
    fnBody("readFolderSortOrdersMirror"),
    fnBody("writeFolderSortOrdersMirror"),
    fnBody("updateFolderSortOrdersMirrorFromList"),
    fnBody("applyFolderSortOrdersMirror"),
    fnBody("readUserFolders"),
    fnBody("writeUserFolders"),
    fnBody("readDocFolderOverrides"),
    fnBody("writeDocFolderOverrides"),
    fnBody("readDocPlacements"),
    fnBody("writeDocPlacements"),
    fnBody("readCustomTreeDocs"),
    fnBody("writeCustomTreeDocs"),
    fnBody("isCustomTreeDoc"),
    fnBody("toggleDocCustomTree"),
    fnBody("getEffectiveDocParentId"),
    `var MAX_FOLDER_DEPTH = 8;`,
    fnBody("isFolderDescendantOrSelf"),
    fnBody("getFolderParentDepth"),
    fnBody("getFolderSubtreeHeight"),
    fnBody("canPlaceFolderUnderParent"),
    fnBody("collectFolderSubtreeIds"),
    fnBody("cloneFolderJson"),
    fnBody("readLocalTrash"),
    fnBody("writeLocalTrash"),
    fnBody("snapshotFolderWorkspace"),
    fnBody("beginFolderUndo"),
    fnBody("commitFolderUndo"),
    fnBody("restoreFolderWorkspace"),
    fnBody("undoLastFolderOrganizeAction"),
    fnBody("redoLastFolderOrganizeAction"),
    fnBody("isQuestionInDocFolder"),
    fnBody("collectUserFolderQuestionIds"),
    fnBody("collectMountedSubfolderQuestionIds"),
    fnBody("collectDocFolderQuestionIds"),
    fnBody("buildUserFolderChildMap"),
    fnBody("buildUserFolderTreeNodesForParent"),
    fnBody("buildUserFolderTreeNodes"),
    fnBody("strongChapterAnchor"),
    fnBody("textbookGroupKey"),
    fnBody("parseBookSectionParts"),
    fnBody("isCompoundBookQuestions"),
    `const PEP_A_COMPULSORY_1_CHAPTERS = { 1: '第1章 集合与常用逻辑用语' };`,
    `const PEP_A_COMPULSORY_1_SECTIONS = [{ ch: 1, lo: 1, hi: 20, label: '1.1 集合的概念' }];`,
    fnBody("isPepACompulsory1Book"),
    fnBody("textbookChapterTitleOf"),
    fnBody("chapterOfQuestion"),
    fnBody("sectionOfQuestion"),
    fnBody("assignTextbookChapters"),
    fnBody("parseTextbookTitle"),
    fnBody("buildPaperTreeNodes"),
    fnBody("buildPlacedDocTreeNodesForParent"),
    fnBody("buildTextbookTreeNodes"),
    fnBody("getFolderTreeRoots"),
    fnBody("renderFolderTreeNode"),
    `function renderFolderTree() {
      const container = document.getElementById('folderTreeContainer');
      if (!container) return;
      folderNodeRegistry.clear();
      folderParentRegistry.clear();
      const roots = getFolderTreeRoots();
      const htmlOut = roots.map(r => renderFolderTreeNode(r, 0, null)).filter(Boolean).join('');
      container.innerHTML = htmlOut || '';
    }`,
    fnBody("resolveQuestionTextbookLeafInfo"),
    fnBody("moveDocFolder"),
    fnBody("nextUnifiedSiblingSortOrder"),
    fnBody("reorderUnifiedSiblingNode"),
    fnBody("expandFolderAncestors"),
    fnBody("getUserFolderFullPath"),
    fnBody("moveUserFolderToTrash"),
    fnBody("getFolderDropZoneFromEvent"),
    fnBody("resolveFolderDropZone"),
    fnBody("canAcceptFolderDrop"),
    fnBody("handleFolderDrop"),
  ];

  vm.runInNewContext(snippets.join("\n\n"), context);
  return context;
}

test("Eagle 大一统数据驱动文件夹树", async (t) => {
  await t.test("1. MAX_FOLDER_DEPTH 为 8 层（8 层允许，第 9 层拒绝）", () => {
    const folders = [];
    for (let i = 1; i <= 8; i += 1) {
      folders.push({
        id: `uf_${i}`,
        name: `第${i}层`,
        parentId: i === 1 ? null : `uf_${i - 1}`,
        questionIds: [],
        createdAt: i,
      });
    }
    const ctx = createUnifiedTreeHarness({ userFolders: folders });
    assert.equal(ctx.MAX_FOLDER_DEPTH, 8);
    assert.equal(ctx.canPlaceFolderUnderParent(folders, null, "uf_7", false), true);
    assert.equal(ctx.canPlaceFolderUnderParent(folders, null, "uf_8", false), false);
  });

  await t.test("2. 书本启用 custom_tree_docs 后彻底跳过硬编码页码表与「 · 」拆分，直接渲染数据驱动多级目录", () => {
    const doc = {
      id: "pep_a1",
      name: "（A版）必修 第一册.pdf",
      filename: "（A版）必修 第一册.pdf",
      materialType: "book",
      published: 2,
      createdAt: new Date("2025-05-01"),
    };
    const qs = [
      { id: "q1", documentId: "pep_a1", section: "1.1 集合的概念", paperPage: 5, number: 1 },
      { id: "q2", documentId: "pep_a1", section: "思考", paperPage: 45, number: 2 },
    ];
    const folders = [
      { id: "uf_part1", name: "自定义篇一", parentId: "book:pep_a1", questionIds: [], createdAt: 10, sortOrder: 10 },
      { id: "uf_part2", name: "自定义篇二（空目录）", parentId: "book:pep_a1", questionIds: [], createdAt: 20, sortOrder: 0 },
      { id: "uf_sec1", name: "集合核心题型", parentId: "uf_part1", questionIds: ["q1"], createdAt: 30, sortOrder: 0 },
      { id: "uf_sub1", name: "题型1 · 子集计数", parentId: "uf_sec1", questionIds: ["q1", "q2"], createdAt: 40, sortOrder: 0 },
    ];
    const ctx = createUnifiedTreeHarness({
      papersData: [doc],
      questionsData: qs,
      userFolders: folders,
      customTreeDocs: ["pep_a1"],
      sortModes: { "book:pep_a1": "manual" },
    });
    ctx.renderFolderTree();

    const bookNode = ctx.folderNodeRegistry.get("book:pep_a1");
    assert.ok(bookNode, "应渲染教材节点");
    assert.deepEqual(
      Array.from(bookNode.children, (c) => c.label),
      ["自定义篇二（空目录）", "自定义篇一"],
      "应严格按 sortOrder 渲染自定义目录（含空目录），不走硬编码第1章/5章"
    );
    assert.equal(bookNode.children[1].children[0].children[0].label, "题型1 · 子集计数");
    assert.equal(bookNode.children[1].children[0].count, 2);

    const leaf = ctx.resolveQuestionTextbookLeafInfo(qs[0], doc);
    assert.equal(leaf.sectionName, "", "接管后不再按硬编码页码表制造第二套章节路径");
  });

  await t.test("3. 资料节点可自由归入自建文件夹或根目录，支持混合手动拖排及 Ctrl+Z 撤销", () => {
    const docBook = {
      id: "b1",
      name: "高中数学思想方法导引",
      materialType: "book",
      published: 2,
      createdAt: new Date("2025-05-01"),
    };
    const docExam = {
      id: "p1",
      name: "高一上期末卷",
      materialType: "exam",
      published: 1,
      createdAt: new Date("2025-06-01"),
    };
    const qs = [
      { id: "q1", documentId: "b1", section: "", number: 1 },
      { id: "q2", documentId: "b1", section: "", number: 2 },
      { id: "q3", documentId: "p1", section: "", number: 1 },
    ];
    const folders = [
      { id: "uf_topic", name: "一轮复习总览", parentId: null, questionIds: [], createdAt: 100, sortOrder: 0 },
      { id: "uf_inner", name: "补充好题", parentId: "uf_topic", questionIds: ["q3"], createdAt: 200, sortOrder: 10 },
    ];
    const ctx = createUnifiedTreeHarness({
      papersData: [docBook, docExam],
      questionsData: qs,
      userFolders: folders,
    });
    ctx.renderFolderTree();

    // Drag book into topic folder
    assert.equal(ctx.moveDocFolder("book:b1", "uf_topic"), true);
    ctx.renderFolderTree();

    const topicNode = ctx.folderNodeRegistry.get("userfolder:uf_topic");
    assert.ok(topicNode, "主题夹应存在");
    assert.equal(topicNode.count, 3, "父夹计数应自动汇总内部书本的 2 题 + 子夹的 1 题");
    assert.equal(ctx.folderNodeRegistry.get("group:books"), undefined, "所有教材均移走后「教材版本」分组自动收起");

    // Mix-reorder book before inner userfolder inside topic folder
    assert.equal(ctx.reorderUnifiedSiblingNode("book:b1", "uf_inner", "before"), true);
    ctx.renderFolderTree();
    const updatedTopic = ctx.folderNodeRegistry.get("userfolder:uf_topic");
    assert.deepEqual(
      Array.from(updatedTopic.children, (c) => c.id),
      ["book:b1", "userfolder:uf_inner"]
    );

    const plain = (v) => JSON.parse(JSON.stringify(v));

    // Ctrl+Z undoes reorder, then undoes moveDocFolder
    assert.equal(ctx.undoLastFolderOrganizeAction(), true);
    assert.equal(ctx.undoLastFolderOrganizeAction(), true);
    assert.deepEqual(plain(ctx.readDocPlacements()), {});

    // Deleting folder containing a placed doc releases the doc safely back to default group
    ctx.moveDocFolder("book:b1", "uf_topic");
    ctx.moveUserFolderToTrash("uf_topic");
    assert.deepEqual(plain(ctx.readDocPlacements()), {});
    ctx.undoLastFolderOrganizeAction();
    assert.equal(ctx.readDocPlacements().b1.parentId, "uf_topic");
  });

  await t.test("4. applyServerFoldersState 正确同步 doc_placements 与 custom_tree_docs", () => {
    const ctx = createUnifiedTreeHarness();
    ctx.applyServerFoldersState({
      revision: 9,
      user_folders: [],
      doc_overrides: {},
      doc_placements: { b1: { parentId: "section:folders", sortOrder: 0 } },
      custom_tree_docs: ["b1"],
      smart_folders: [],
      sort_modes: { "section:folders": "manual" },
      paper_sort_mode: "manual",
    });
    const plain = (v) => JSON.parse(JSON.stringify(v));
    assert.deepEqual(plain(ctx.readDocPlacements()), { b1: { parentId: "section:folders", sortOrder: 0 } });
    assert.deepEqual(plain(ctx.readCustomTreeDocs()), ["b1"]);
    assert.equal(ctx.libraryFoldersRevision, 9);
  });
});
