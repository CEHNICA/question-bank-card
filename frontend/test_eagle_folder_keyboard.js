"use strict";
// 点过文件夹后，上下键更换文件夹，Enter 或 F2 重命名。
// Ctrl+左右不参与文件夹移动。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { describe, it } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function extractNavSource() {
  const start = html.indexOf("// --- folder-keyboard-nav ---");
  const end = html.indexOf("// --- /folder-keyboard-nav ---");
  assert.ok(start >= 0 && end > start, "应能抽出文件夹键盘导航函数");
  return html.slice(start, end);
}

function makeClassList() {
  const classes = new Set();
  return {
    add(name) { classes.add(name); },
    remove(name) { classes.delete(name); },
    contains(name) { return classes.has(name); },
    toggle(name, on) {
      if (on) classes.add(name);
      else classes.delete(name);
    },
  };
}

function loadNav(rows) {
  const sandbox = {
    folderTreeState: { activeId: "all", expanded: new Set(), search: "" },
    folderNodeRegistry: new Map(),
    folderParentRegistry: new Map(),
    applyFolderNode(id) {
      sandbox.folderTreeState.activeId = id;
      if (typeof sandbox.settleFolderKeyboardAfterApply === "function") {
        sandbox.settleFolderKeyboardAfterApply();
      }
    },
    navigateQuestionFocus() {
      sandbox.questionMoves += 1;
    },
    questionMoves: 0,
    currentActiveQuestionId: "",
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === "folderTreeContainer") return sandbox.container;
        if (id === "originalPaperModal" || id === "quickLookModal") {
          return sandbox.modals[id];
        }
        const row = (rows || []).find((item) => item.id === id);
        return row || null;
      },
    },
    modals: {
      originalPaperModal: { classList: { contains(name) { return name === "hidden"; } } },
      quickLookModal: { classList: { contains(name) { return name === "hidden"; } } },
    },
    container: {
      contains(el) { return (rows || []).includes(el); },
      querySelectorAll(sel) {
        const list = rows || [];
        if (sel === "[data-folder-enterable=\"1\"]") return list;
        if (sel === ".ftree-kbd-cursor") return list.filter((row) => row.classList.contains("ftree-kbd-cursor"));
        return [];
      },
    },
  };
  vm.runInNewContext(extractNavSource(), sandbox);
  return sandbox;
}

function makeRow(nodeId) {
  return {
    id: "ftree-" + nodeId,
    classList: makeClassList(),
    focus() { this.owner.activeElement = this; },
    blur() {
      if (this.owner && this.owner.activeElement === this) this.owner.activeElement = null;
    },
    scrollIntoView() {},
  };
}

describe("文件夹键盘导航", () => {
  it("点过文件夹后，上下键直接换成相邻文件夹，Enter 重命名当前文件夹", () => {
    const parent = makeRow("parent");
    const child = makeRow("child");
    const nav = loadNav([parent, child]);
    parent.owner = nav.document;
    child.owner = nav.document;
    nav.folderNodeRegistry.set("parent", { id: "parent", type: "userfolder" });
    nav.folderNodeRegistry.set("child", { id: "child", type: "paper" });
    nav.folderTreeState.activeId = "parent";
    nav.document.activeElement = null;
    nav.armFolderKeyboardIfTreeEvent({ type: "click", target: parent });

    const handled = nav.handleFolderKeyboardEvent({
      key: "ArrowDown",
      preventDefault() {},
    });
    assert.equal(handled, true);
    assert.equal(nav.folderTreeState.activeId, "child");
    assert.equal(child.classList.contains("ftree-kbd-cursor"), true);
    assert.equal(nav.questionMoves, 0);

    const renamed = [];
    nav.beginInlineTreeRename = (id) => renamed.push(id);
    const entered = nav.handleFolderKeyboardEvent({
      key: "Enter",
      preventDefault() {},
    });
    assert.equal(entered, true);
    assert.deepEqual(renamed, ["child"]);
    assert.equal(nav.questionMoves, 0);
  });

  it("Ctrl+左回到上一题，Ctrl+右回到刚才那道题，不跟着文件夹走丢", () => {
    const nav = loadNav([]);
    const focus = (id) => {
      nav.noteQuestionVisit(id);
      nav.currentActiveQuestionId = id;
    };
    nav.located = [];
    nav.locateQuestionById = (id) => {
      nav.located.push(id);
      nav.currentActiveQuestionId = id;
    };
    focus("a");
    focus("b");
    focus("c");

    assert.equal(nav.goQuestionHistoryBack(), true);
    assert.deepEqual(nav.located, ["b"]);
    assert.equal(nav.goQuestionHistoryBack(), true);
    assert.equal(nav.currentActiveQuestionId, "a");
    assert.equal(nav.goQuestionHistoryForward(), true);
    assert.equal(nav.currentActiveQuestionId, "b");
    assert.equal(nav.folderTreeState.activeId, "all");
  });

  it("Ctrl+左右不换文件夹；没点过题时不拦截，审题弹窗里也不拦截上下键", () => {
    const parent = makeRow("parent");
    const nav = loadNav([parent]);
    parent.owner = nav.document;
    nav.folderNodeRegistry.set("parent", { id: "parent", type: "userfolder" });
    nav.folderNodeRegistry.set("child", { id: "child", type: "paper" });
    nav.folderTreeState.activeId = "child";
    nav.document.activeElement = null;

    assert.equal(nav.handleFolderKeyboardEvent({ key: "ArrowDown", preventDefault() {} }), false);
    assert.equal(nav.folderTreeState.activeId, "child");

    nav.armFolderKeyboardIfTreeEvent({ type: "click", target: parent });
    let prevented = false;
    assert.equal(nav.handleFolderKeyboardEvent({
      key: "ArrowLeft",
      ctrlKey: true,
      preventDefault() { prevented = true; },
    }), false);
    assert.equal(prevented, false);
    assert.equal(nav.folderTreeState.activeId, "child");

    nav.modals.quickLookModal.classList.contains = () => false;
    assert.equal(nav.handleFolderKeyboardEvent({
      key: "ArrowDown",
      preventDefault() {},
    }), false);
  });

  it("页面保留上下键换文件夹，Enter 和 F2 都重命名", () => {
    const keydownAt = html.indexOf("document.addEventListener('keydown'");
    const handleAt = html.indexOf("if (handleFolderKeyboardEvent(e)) return;", keydownAt);
    const questionAt = html.indexOf("navigateQuestionFocus(1)", handleAt);
    assert.ok(handleAt > keydownAt);
    assert.ok(questionAt > handleAt);
    assert.doesNotMatch(html, /function goFolderHistoryBack\(/);
    assert.doesNotMatch(html, /id="btnFolderNavBack"/);
    assert.match(html, /function noteQuestionVisit\(nextId\)/);
    assert.match(html, /noteQuestionVisit\(qid\)/);
    assert.match(html, /返回上一题/);
    assert.match(html, /Ctrl \+ ←/);
    assert.match(html, /Ctrl \+ →/);
    assert.match(html, /tabindex="-1" data-folder-enterable="1"/);
    assert.match(html, /点过文件夹后，上下键更换文件夹，Enter 或 F2 重命名/);
    assert.match(html, /armFolderKeyboardIfTreeEvent\(/);
  });

  it("文件夹按 F2 改名，双击只收起自己和全部子文件夹", () => {
    assert.match(html, /ondblclick="event\.stopPropagation\(\); collapseFolderBranch\(/);
    assert.doesNotMatch(html, /ondblclick="event\.stopPropagation\(\); beginInlineTreeRename\(/);
    assert.doesNotMatch(html, /重命名此入库项目 \(或双击\)/);
    assert.doesNotMatch(html, /ondblclick="startRenamePaper/);
    assert.doesNotMatch(html, /function enterFolderKeyboardCursor\(/);
    assert.match(html, /请在左侧文件夹上按 F2，或右键选择重命名/);

    const parent = makeRow("parent");
    const nav = loadNav([parent]);
    parent.owner = nav.document;
    nav.folderNodeRegistry.set("parent", { id: "parent", type: "userfolder" });
    nav.folderTreeState.activeId = "parent";
    nav.armFolderKeyboardIfTreeEvent({ type: "click", target: parent });
    const renamed = [];
    nav.beginInlineTreeRename = (id) => renamed.push(id);
    let prevented = false;
    assert.equal(nav.handleFolderKeyboardEvent({
      key: "F2",
      preventDefault() { prevented = true; },
    }), true);
    assert.equal(prevented, true);
    assert.deepEqual(renamed, ["parent"]);

    const start = html.indexOf("function collapseFolderBranch(");
    const end = html.indexOf("function toggleFolderNode(", start);
    const sandbox = {
      folderNodeRegistry: new Map(),
      folderTreeState: { expanded: new Set(["parent", "child", "grand"]) },
      writes: 0,
      renders: 0,
      writeExpandedFolderNodes() { sandbox.writes += 1; },
      renderFolderTree() { sandbox.renders += 1; },
    };
    const child = { id: "child", children: [{ id: "grand", children: [] }] };
    sandbox.folderNodeRegistry.set("parent", { id: "parent", children: [child] });
    vm.runInNewContext(html.slice(start, end), sandbox);
    sandbox.collapseFolderBranch("parent");
    assert.equal(sandbox.folderTreeState.expanded.has("parent"), false);
    assert.equal(sandbox.folderTreeState.expanded.has("child"), false);
    assert.equal(sandbox.folderTreeState.expanded.has("grand"), false);
    assert.equal(sandbox.writes, 1);
    sandbox.collapseFolderBranch("parent");
    assert.equal(sandbox.writes, 1, "已经收起时不再重画整棵树");
  });
});
