"use strict";

// 1.12.5：批量处理题卡整套删除，这道题只保留"单卡移到回收站"这一条路。
// 下面钉的都是还在的：软删除、撤销、按批次恢复，以及"等服务端成功再从
// 页面移除题卡"这条不变量（1.10 的一次真实丢题就是漏了它）。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// 批量选择那套必须已经不在了：标记、样式、脚本三处都不能留残骸。
for (const id of ["selectionStart", "selectionBar", "selectionCount", "selectionHint",
  "selectionCancel", "selectionReread", "selectionDelete"]) {
  assert.doesNotMatch(html, new RegExp(`id="${id}"`), `${id} 应已随批量处理一起删除`);
}
assert.doesNotMatch(js, /updateSelection|startSelecting|stopSelecting|renderSelectionState|cardSelectionControl/);
assert.doesNotMatch(css, /card-select|selection-bar|card\.is-selected/);
// 已入库的题不能删，删之前要说清楚原因。
assert.match(js, /这道题已经入库，为保留来源和版本记录，不能从审题任务中删除/);
assert.match(js, /题卡会从当前审题列表移走，但不会立即永久清除/);

// 接口成功前不从本地列表乐观移除。
assert.match(js, /await api\(`\/api\/papers\/\$\{paperId\}\/questions\/delete`/);
const requestAt = js.indexOf("await api(`/api/papers/${paperId}/questions/delete`");
const localRemoveAt = js.indexOf("state.questions = state.questions.filter", requestAt);
assert.ok(requestAt >= 0 && localRemoveAt > requestAt, "必须等服务端成功后再从页面移除题卡");

// 软删除可撤销，回收站刷新后仍能按批次恢复。
assert.match(js, /softDeleteQuestions\(\[q\.id\], \{ singleQuestion: q \}\)/);
assert.match(js, /label: "撤销"[\s\S]*restoreDeletedBatch\(paperId, batchId\)/);
assert.match(js, /\/api\/papers\/\$\{paperId\}\/question-trash`/);
assert.match(js, /\/api\/papers\/\$\{paperId\}\/question-trash\/\$\{batchId\}\/restore/);
assert.match(js, /回收站里还有题卡；请先恢复这些题卡，再归档任务/);
assert.match(js, /回收站里还有题卡；请先恢复这些题卡，再调整页序/);
for (const id of ["questionTrash", "trashDialog", "trashList"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}

console.log("question delete UI checks: OK");
