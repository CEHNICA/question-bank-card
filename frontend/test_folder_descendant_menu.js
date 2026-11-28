"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const html = fs.readFileSync(path.join(__dirname, "eagle/index.html"), "utf8");
const fn = (name) => html.match(new RegExp(`    function ${name}\\([\\s\\S]*?\\n    \\}`))[0];
function harness() {
  const grandchild = { id: "grandchild", children: [{ id: "leaf", children: [] }] };
  const child = { id: "child", children: [grandchild] };
  const root = { id: "root", children: [child] };
  const counters = { save: 0, render: 0, close: 0 };
  const context = vm.createContext({ folderNodeRegistry: new Map([["root", root], ["leaf", grandchild.children[0]]]),
    folderTreeState: { expanded: new Set(["root", "child", "grandchild", "unrelated"]), activeId: "grandchild" },
    writeExpandedFolderNodes() { counters.save++; }, renderFolderTree() { counters.render++; },
    hideEagleContextMenu() { counters.close++; }, showToast() {},
    escapeInlineJsArg: (id) => id,
    treeMenuItem: (icon, label, action) => `<button onclick="${action}">${label}</button>`,
  });
  vm.runInContext(["setFolderDescendantsExpanded", "folderDescendantMenu", "treeExpandCollapseLabels"].map(fn).join("\n"), context);
  return { context, counters, root };
}
test("收起子目录仅修改当前分支，保留父文件夹、其他目录和当前选中题目", () => {
  const { context: c, counters } = harness();
  c.setFolderDescendantsExpanded("root", false);
  assert.deepEqual([...c.folderTreeState.expanded], ["root", "unrelated"]);
  assert.equal(c.folderTreeState.activeId, "grandchild");
  assert.deepEqual(counters, { save: 1, render: 1, close: 1 });
  c.setFolderDescendantsExpanded("root", true);
  assert.equal(c.folderTreeState.expanded.has("child"), true);
  assert.equal(c.folderTreeState.expanded.has("grandchild"), true);
  assert.equal(c.folderTreeState.expanded.has("leaf"), false);
  assert.equal(c.folderTreeState.expanded.has("unrelated"), true);
});
test("菜单使用分支动作，标签显示子标签；叶节点不放无效操作", () => {
  const { context: c, root } = harness();
  assert.match(c.folderDescendantMenu("root"), /收起全部子文件夹/);
  assert.match(c.folderDescendantMenu("root"), /setFolderDescendantsExpanded\('root', false\)/);
  assert.doesNotMatch(c.folderDescendantMenu("root"), /collapseAllFolderNodes/);
  root.type = "tag";
  assert.match(c.folderDescendantMenu("root"), /收起全部子标签/);
  assert.equal(c.folderDescendantMenu("leaf"), "");
  assert.match(fn("handleFolderTreeContextMenu"), /folderDescendantMenu\(nodeId\)/);
  assert.match(fn("openEagleContextMenu"), /folderDescendantMenu\(parentKey\)/);
});
test("非法节点和循环引用不会影响其他分支", () => {
  const { context: c, root } = harness();
  c.setFolderDescendantsExpanded("missing", false);
  root.children[0].children.push(root);
  c.setFolderDescendantsExpanded("root", false);
  assert.deepEqual([...c.folderTreeState.expanded], ["root", "unrelated"]);
});
