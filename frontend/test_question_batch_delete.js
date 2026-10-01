"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { updateSelection } = require("./app.js");

const order = [11, 12, 13, 14, 15];

// Ctrl/Cmd 或题卡选择按钮一次只切换一题。
let result = updateSelection({ order, eligible: order, selected: [], target: 12 });
assert.deepEqual(result, { selected: [12], anchor: 12 });
result = updateSelection({ order, eligible: order, selected: result.selected, target: 12, anchor: result.anchor });
assert.deepEqual(result, { selected: [], anchor: 12 });

// Shift 严格按照当前可见顺序取连续范围；普通 Shift 会替换旧范围。
result = updateSelection({ order, eligible: order, selected: [11], target: 15, anchor: 12, range: true });
assert.deepEqual(result, { selected: [12, 13, 14, 15], anchor: 12 });

// 已入库或处理中等不可删除题卡不会被范围选择带进去。
result = updateSelection({ order, eligible: [11, 12, 14, 15], selected: [], target: 15, anchor: 11, range: true });
assert.deepEqual(result, { selected: [11, 12, 14, 15], anchor: 11 });

// Ctrl/Cmd+Shift 可以把新范围加入已有选择；重渲染时已不存在的 id 会被清理。
result = updateSelection({ order, eligible: order, selected: [11, 999], target: 15, anchor: 13, range: true, additive: true });
assert.deepEqual(result, { selected: [11, 13, 14, 15], anchor: 13 });

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// 选择只改变本地选择状态，真正删除必须经过显眼选择栏和确认框。
for (const id of ["selectionBar", "selectionCount", "selectionCancel", "selectionDelete"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}
assert.match(js, /Ctrl\/Cmd 点击增减单题，Shift 点击选择连续范围/);
assert.match(js, /把选中的 \$\{count\} 道题移到回收站/);
assert.match(js, /题卡会从当前审题列表移走，但不会立即永久清除/);
assert.match(js, /event\.target\.closest\?\.\("button, a, summary, input, textarea, select/);

// 只有 ready 阶段可选；接口成功前不从本地列表乐观移除。
assert.match(js, /state\.paper\?\.status !== "ready"/);
assert.match(js, /await api\(`\/api\/papers\/\$\{paperId\}\/questions\/delete`/);
const requestAt = js.indexOf("await api(`/api/papers/${paperId}/questions/delete`");
const localRemoveAt = js.indexOf("state.questions = state.questions.filter", requestAt);
assert.ok(requestAt >= 0 && localRemoveAt > requestAt, "必须等服务端成功后再从页面移除题卡");

// 单删和批删使用同一套可撤销软删除；回收站刷新后仍可按批次恢复。
assert.match(js, /softDeleteQuestions\(\[q\.id\], \{ singleQuestion: q \}\)/);
assert.match(js, /label: "撤销"[\s\S]*restoreDeletedBatch\(paperId, batchId\)/);
assert.match(js, /\/api\/papers\/\$\{paperId\}\/question-trash`/);
assert.match(js, /\/api\/papers\/\$\{paperId\}\/question-trash\/\$\{batchId\}\/restore/);
assert.match(js, /回收站里还有题卡；请先恢复这些题卡，再归档任务/);
assert.match(js, /回收站里还有题卡；请先恢复这些题卡，再调整页序/);
for (const id of ["questionTrash", "trashDialog", "trashList"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}

// 视觉上必须能分清已选卡片，并且手机上选择栏不溢出。
assert.match(css, /\.card\.is-selected\s*\{/);
assert.match(css, /\.card-select\[aria-pressed="true"\]/);
assert.match(css, /@media \(max-width: 540px\)[\s\S]*?\.selection-bar\s*\{[^}]*flex-direction:\s*column/);

console.log("question batch delete UI checks: OK");
