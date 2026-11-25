"use strict";
// Eagle 文件夹整理 Ctrl+Z / Ctrl+Y：拖题进夹等操作可撤销
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

test("文件夹整理撤销栈", async (t) => {
  await t.test("1. 快捷键与文案已接入", () => {
    assert.match(html, /function undoLastFolderOrganizeAction\(/);
    assert.match(html, /function redoLastFolderOrganizeAction\(/);
    assert.match(html, /Ctrl\+Z 撤销/);
    assert.match(html, /undoLastFolderOrganizeAction\(\)/);
    assert.match(html, /redoLastFolderOrganizeAction\(\)/);
    assert.match(html, /撤销 \/ 重做整理/);
  });

  await t.test("2. 放入文件夹后 Ctrl+Z 能恢复题号归属", () => {
    const store = new Map();
    store.set(
      "eagle_user_folders_v1",
      JSON.stringify([
        { id: "uf_a", name: "错题本", parentId: null, questionIds: [], createdAt: 1, sortOrder: 0 },
      ])
    );
    store.set("eagle_custom_smart_folders", JSON.stringify([]));
    store.set("eagle_doc_folder_overrides_v1", JSON.stringify({}));
    store.set("folder_sort_modes_v1", JSON.stringify({}));
    store.set("paper_sort_mode", "time_desc");

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
      localStorage: {
        getItem(k) {
          return store.has(k) ? store.get(k) : null;
        },
        setItem(k, v) {
          store.set(k, String(v));
        },
        removeItem(k) {
          store.delete(k);
        },
      },
      console,
      window: {},
      document: { getElementById() { return null; } },
      USER_FOLDERS_STORAGE_KEY: "eagle_user_folders_v1",
      CUSTOM_SMART_FOLDERS_STORAGE_KEY: "eagle_custom_smart_folders",
      EAGLE_DOC_FOLDER_OVERRIDES_KEY: "eagle_doc_folder_overrides_v1",
      FOLDER_SORT_MODES_STORAGE_KEY: "folder_sort_modes_v1",
      FOLDER_SORT_ORDERS_MIRROR_KEY: "eagle_folder_sort_orders_v1",
      EAGLE_LAST_FOLDER_KEY: "eagle_last_folder",
      VALID_FOLDER_SORT_MODES: new Set(["time_desc", "time_asc", "name", "count_desc", "manual"]),
      FOLDER_UNDO_LIMIT: 40,
      folderOrganizeUndoStack: [],
      folderOrganizeRedoStack: [],
      _suppressFolderUndoCapture: false,
      _suppressFoldersSync: false,
      currentPaperSort: "time_desc",
      folderSortModes: {},
      filterState: { userFolderId: "", smartFilter: "" },
      folderTreeState: { expanded: new Set(), activeId: "all" },
      questionsData: [],
      papersData: [],
      showToast(msg, kind) {
        toasts.push({ msg, kind });
      },
      scheduleFoldersSync() {},
      renderFolderTree() {},
      renderRightInspector() {},
      applyFilters() {},
      sortAndRenderPapers() {},
      hideEagleContextMenu() {},
      folderCreatedAtMs(x) {
        return Number(x && x.createdAt) || 0;
      },
      collectFolderSubtreeIds(list, folderId) {
        return new Set([folderId]);
      },
      toasts,
    };

    vm.runInNewContext(
      [
        fnBody("cloneFolderJson"),
        fnBody("readFolderSortOrdersMirror"),
        fnBody("writeFolderSortOrdersMirror"),
        fnBody("updateFolderSortOrdersMirrorFromList"),
        fnBody("applyFolderSortOrdersMirror"),
        fnBody("readUserFolders"),
        fnBody("writeUserFolders"),
        fnBody("readCustomSmartFolders"),
        fnBody("writeCustomSmartFolders"),
        fnBody("readDocFolderOverrides"),
        fnBody("writeDocFolderOverrides"),
        `function readFolderSortModes(){ return folderSortModes || {}; }`,
        `function readLocalTrash(){ return { folders: [], smartFolders: [], questions: [] }; }`,
        `function writeLocalTrash(){}`,
        fnBody("snapshotFolderWorkspace"),
        fnBody("beginFolderUndo"),
        fnBody("commitFolderUndo"),
        fnBody("restoreFolderWorkspace"),
        fnBody("undoLastFolderOrganizeAction"),
        fnBody("redoLastFolderOrganizeAction"),
        fnBody("addQuestionsToUserFolder"),
      ].join("\n\n"),
      context
    );

    assert.equal(context.addQuestionsToUserFolder(["q1", "q2"], "uf_a"), 2);
    let folders = context.readUserFolders();
    assert.deepEqual(folders[0].questionIds.sort(), ["q1", "q2"]);
    assert.match(toasts[toasts.length - 1].msg, /Ctrl\+Z 撤销/);

    assert.equal(context.undoLastFolderOrganizeAction(), true);
    folders = context.readUserFolders();
    assert.deepEqual(folders[0].questionIds, []);
    assert.match(toasts[toasts.length - 1].msg, /已撤销：放入「错题本」/);

    assert.equal(context.redoLastFolderOrganizeAction(), true);
    folders = context.readUserFolders();
    assert.deepEqual(folders[0].questionIds.sort(), ["q1", "q2"]);
    assert.match(toasts[toasts.length - 1].msg, /已重做：放入「错题本」/);
  });
});
