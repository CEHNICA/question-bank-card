"use strict";

// 批量审核放在试卷操作菜单，并复用服务端已有的
// approve-green（CLI 也走同一个），并且把服务端返回的"过不去的题和原因"
// 原样摊开——静默跳过就是"点了什么都没发生"。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

assert.match(html, /id="approveAllGreen"[^>]*>批量通过可入库题目…<\/button>/);
const paperOperations = html.slice(html.indexOf('id="paperMenu"'), html.indexOf('id="publishButton"'));
for (const id of ["viewOriginalPaper", "resegment", "pageOrder", "questionTrash", "approveAllGreen"]) {
  assert.ok(paperOperations.includes(`id="${id}"`), `${id} belongs to the paper operations menu`);
}
assert.ok(paperOperations.indexOf('id="questionTrash"') < paperOperations.indexOf('id="approveAllGreen"'), "bulk review is separated after paper organization actions");
assert.doesNotMatch(html, /id="toolsMenu"|id="manualProcessing"/, "paper-level actions have one home and no duplicate cut entry");
const controls = js.slice(js.indexOf("  function syncTrashControls()"), js.indexOf("  function renderTrashBusy("));
assert.match(controls, /\$\("approveAllGreen"\)\.hidden = !state\.paper;/);
assert.match(controls, /\$\("approveAllGreen"\)\.disabled = !ready \|\| !waiting \|\| state\.approveAllBusy;/);
assert.match(js, /\$\("approveAllGreen"\)\.addEventListener\("click", approveAllGreen\);/);

const handler = js.slice(js.indexOf("  async function approveAllGreen()"), js.indexOf("  async function rereadQuestion("));
assert.match(handler, /await api\(`\/api\/papers\/\$\{paperId\}\/approve-green`, \{ method: "POST", body: \{ by: "human" \} \}\)/);
// 先问一句，不点确认就不动数据。
assert.match(handler, /confirmDialog\(\{/);
assert.match(handler, /await confirmDialog\(/);
assert.match(handler, /title: "批量审核这份试卷？"/);
assert.match(handler, /\$\("paperMenu"\)\.open = false;/);
// 结果面板：入库几道 + 逐条原因，两个都不能少。
assert.match(handler, /showApproveResult\(\{/);
const result = js.slice(js.indexOf("  function showApproveResult("), js.indexOf("  async function approveAllGreen("));
// 1.26.41：画面板的事交给统一模块，这里不再自己拼「第 N 题」——
// 同一本教材里题号会重号，光报题号在书上一百多处都对不上。
assert.match(result, /window\.QBApproveResult\?\.render/);
assert.match(result, /render\(list, \{ approved, problems, skipped \}\)/);
assert.doesNotMatch(result, /第 \$\{item\.number\} 题/, "定位改由统一模块负责，不再自己拼题号");
// 模块没加载出来时要说实话，不装作一切正常。
assert.match(result, /没加载出来/);
assert.match(html, /id="approveResultDialog"/);
assert.match(html, /id="approveResultList"/);
// 不谎称全做完：过不去的题永远跟着结果一起报。
assert.match(js, /skipped: data\.skipped \|\| \[\]/);
assert.ok(!js.includes("已通过所有题目"), "不许用「已通过所有题目」这种说法");

// 两个新模块要在 app.js 之前加载（app.js 一上来就要用）。
const locatorScript = html.indexOf('src="/question-locator.js"');
const resultScript = html.indexOf('src="/approve-result.js"');
const appScript = html.indexOf('src="/app.js"');
assert.notEqual(locatorScript, -1, "页面要加载定位模块");
assert.notEqual(resultScript, -1, "页面要加载结果面板模块");
assert.ok(locatorScript < appScript, "定位模块要在 app.js 之前");
assert.ok(resultScript < appScript, "结果面板模块要在 app.js 之前");

// 找题和回收站说的是同一道题，就得用同一套说法。
assert.match(js, /window\.QBQuestionLocator\?\.locate\(q\)/, "找题用统一说法");
assert.match(js, /window\.QBQuestionLocator\?\.locateWithPage\(question\)/, "回收站用统一说法（没章节名时报页码）");
assert.ok(!js.includes("typeof q.group === \"object\" ? q.group?.title : q.section"), "定位别再各写各的");

console.log("approve all green checks: OK");
