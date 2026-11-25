"use strict";
// Eagle 风格文件夹拖拽：同级 before/after 插线 + manual/sortOrder 持久化
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

function createReorderHarness(initialFolders = []) {
  const store = new Map();
  store.set("eagle_user_folders_v1", JSON.stringify(initialFolders));
  store.set("eagle_custom_smart_folders", JSON.stringify([]));
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
    window: {},
    document: {
      querySelectorAll() {
        return [];
      },
      getElementById() {
        return null;
      },
    },
    USER_FOLDERS_STORAGE_KEY: "eagle_user_folders_v1",
    CUSTOM_SMART_FOLDERS_STORAGE_KEY: "eagle_custom_smart_folders",
    FOLDER_SORT_MODES_STORAGE_KEY: "folder_sort_modes_v1",
    VALID_FOLDER_SORT_MODES: new Set(["time_desc", "time_asc", "name", "count_desc", "manual"]),
    MAX_FOLDER_DEPTH: 4,
    currentPaperSort: "time_desc",
    folderSortModes: {},
    folderTreeState: { expanded: new Set(["section:folders"]), activeId: "all", search: "" },
    showToast(msg, kind) {
      toasts.push({ msg, kind });
    },
    scheduleFoldersSync() {},
    writeExpandedFolderNodes() {},
    renderFolderTree() {},
    hideEagleContextMenu() {},
    sortAndRenderPapers() {},
    beginFolderUndo(label) {
      return { label, before: null };
    },
    commitFolderUndo() {},
    toasts,
  };

  const snippets = [
    // 用 sandbox 属性，避免 let 遮蔽导致断言读不到写入结果
    fnBody("readFolderSortModes"),
    fnBody("writeFolderSortModes"),
    fnBody("setPaperSortMode"),
    fnBody("setFolderSortMode"),
    `const ZH_NUM_DIGITS = {'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};`,
    `const ZH_NUM_UNITS = {'十':10,'百':100,'千':1000};`,
    fnBody("parseChineseNumeral"),
    fnBody("normalizeLabelForNaturalSort"),
    fnBody("compareNaturalZhLabels"),
    fnBody("folderCreatedAtMs"),
    fnBody("compareFolderTreeNodes"),
    fnBody("sortFolderTreeNodes"),
    fnBody("isSystemFolderParentId"),
    fnBody("isFolderDescendantOrSelf"),
    fnBody("getFolderParentDepth"),
    fnBody("getFolderSubtreeHeight"),
    fnBody("canPlaceFolderUnderParent"),
    fnBody("isValidUserFolderParent"),
    `const FOLDER_SORT_ORDERS_MIRROR_KEY = 'eagle_folder_sort_orders_v1';`,
    fnBody("readFolderSortOrdersMirror"),
    fnBody("writeFolderSortOrdersMirror"),
    fnBody("updateFolderSortOrdersMirrorFromList"),
    fnBody("applyFolderSortOrdersMirror"),
    fnBody("readUserFolders"),
    fnBody("writeUserFolders"),
    fnBody("folderSortParentKeyFromParentId"),
    fnBody("renumberSiblingSortOrders"),
    fnBody("reorderFolderListByAnchor"),
    fnBody("nextSiblingSortOrder"),
    fnBody("reorderUserFolderAmongSiblings"),
    fnBody("readCustomSmartFolders"),
    fnBody("writeCustomSmartFolders"),
    fnBody("reorderCustomSmartFolderAmongSiblings"),
    fnBody("getFolderDropZoneFromEvent"),
    fnBody("resolveFolderDropZone"),
    fnBody("canAcceptFolderDrop"),
  ];
  vm.runInNewContext(snippets.join("\n\n"), context);
  return context;
}

test("Eagle 文件夹拖拽同级排序", async (t) => {
  await t.test("1. HTML 含 before/after 插线样式与拖拽结束钩子", () => {
    assert.match(html, /ftree-drop-before::before/);
    assert.match(html, /ftree-drop-after::after/);
    assert.match(html, /手动排序 \(拖拽位置\)/);
    assert.match(html, /ondragend="handleFolderDragEnd\(event\)"/);
    assert.match(html, /function reorderUserFolderAmongSiblings\(/);
    assert.match(html, /VALID_FOLDER_SORT_MODES = new Set\(\[[^\]]*manual/);
  });

  await t.test("2. 指针分区：上 before / 中 into / 下 after", () => {
    const ctx = createReorderHarness();
    const el = {
      getBoundingClientRect() {
        return { top: 100, height: 40, left: 0, width: 120, bottom: 140, right: 120 };
      },
    };
    assert.equal(ctx.getFolderDropZoneFromEvent({ clientY: 105 }, el), "before");
    assert.equal(ctx.getFolderDropZoneFromEvent({ clientY: 120 }, el), "into");
    assert.equal(ctx.getFolderDropZoneFromEvent({ clientY: 135 }, el), "after");
  });

  await t.test("3. before/after 重写 sortOrder 并切到 manual", () => {
    const ctx = createReorderHarness([
      { id: "uf_a", name: "甲", parentId: null, questionIds: [], createdAt: 100, sortOrder: 0 },
      { id: "uf_b", name: "乙", parentId: null, questionIds: [], createdAt: 200, sortOrder: 10 },
      { id: "uf_c", name: "丙", parentId: null, questionIds: [], createdAt: 300, sortOrder: 20 },
    ]);
    assert.equal(ctx.reorderUserFolderAmongSiblings("uf_c", "uf_a", "before"), true);
    const list = ctx.readUserFolders();
    const ordered = list
      .filter((f) => !f.parentId)
      .slice()
      .sort((a, b) => a.sortOrder - b.sortOrder)
      .map((f) => f.id);
    assert.deepEqual(ordered, ["uf_c", "uf_a", "uf_b"]);
    assert.equal(ctx.folderSortModes["section:folders"], "manual");
    assert.equal(
      ctx.compareFolderTreeNodes(
        { label: "丙", sortOrder: 0, createdAtMs: 300 },
        { label: "甲", sortOrder: 10, createdAtMs: 100 },
        "manual"
      ) < 0,
      true
    );
  });

  await t.test("4. 拖到另一夹上缘=改父级并插入其同级，拖到中间=只能嵌套", () => {
    const ctx = createReorderHarness([
      { id: "uf_root", name: "根", parentId: null, questionIds: [], createdAt: 1, sortOrder: 0 },
      { id: "uf_sib", name: "兄", parentId: null, questionIds: [], createdAt: 2, sortOrder: 10 },
      { id: "uf_child", name: "子", parentId: "uf_root", questionIds: [], createdAt: 3, sortOrder: 0 },
    ]);
    const payload = { kind: "folder", folderType: "userfolder", itemId: "uf_sib" };
    const targetChild = { type: "userfolder", folderId: "uf_child", id: "userfolder:uf_child" };
    const targetRoot = { type: "userfolder", folderId: "uf_root", id: "userfolder:uf_root" };
    assert.equal(ctx.canAcceptFolderDrop(payload, targetRoot, "into"), true);
    assert.equal(ctx.canAcceptFolderDrop(payload, targetRoot, "before"), true);
    assert.equal(ctx.canAcceptFolderDrop(payload, targetChild, "before"), true);
    assert.equal(ctx.reorderUserFolderAmongSiblings("uf_sib", "uf_child", "after"), true);
    const list = ctx.readUserFolders();
    const sib = list.find((f) => f.id === "uf_sib");
    const child = list.find((f) => f.id === "uf_child");
    assert.equal(sib.parentId, "uf_root");
    assert.ok(sib.sortOrder > child.sortOrder);
  });
});
