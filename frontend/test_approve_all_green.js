"use strict";

// 1.12.5：工具菜单里的「一键通过所有题目」。它必须复用服务端已有的
// approve-green（CLI 也走同一个），并且把服务端返回的"过不去的题和原因"
// 原样摊开——静默跳过就是"点了什么都没发生"。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

assert.match(html, /id="approveAllGreen"[^>]*>一键通过所有题目<\/button>/);
// 入口和「题卡回收站」并排，但只有真有能通过的题时才可点。
const tools = html.slice(html.indexOf('id="toolsMenu"'), html.indexOf("</details>", html.indexOf('id="toolsMenu"')));
assert.doesNotMatch(tools, /批量处理题卡/);
const controls = js.slice(js.indexOf("  function syncTrashControls()"), js.indexOf("  function renderTrashBusy("));
assert.match(controls, /\$\("approveAllGreen"\)\.hidden = !state\.paper;/);
assert.match(controls, /\$\("approveAllGreen"\)\.disabled = !ready \|\| !waiting \|\| state\.approveAllBusy;/);
assert.match(js, /\$\("approveAllGreen"\)\.addEventListener\("click", approveAllGreen\);/);

const handler = js.slice(js.indexOf("  async function approveAllGreen()"), js.indexOf("  async function rereadQuestion("));
assert.match(handler, /await api\(`\/api\/papers\/\$\{paperId\}\/approve-green`, \{ method: "POST", body: \{ by: "human" \} \}\)/);
// 先问一句，不点确认就不动数据。
assert.match(handler, /confirmDialog\(\{/);
assert.match(handler, /await confirmDialog\(/);
// 结果面板：入库几道 + 逐条原因，两个都不能少。
assert.match(handler, /showApproveResult\(\{/);
const result = js.slice(js.indexOf("  function showApproveResult("), js.indexOf("  async function approveAllGreen("));
assert.match(result, /已入库 \$\{approved\} 道/);
assert.match(result, /approve-result-skip/);
assert.match(result, /第 \$\{item\.number\} 题：\$\{item\.reason\}/);
assert.match(html, /id="approveResultDialog"/);
assert.match(html, /id="approveResultList"/);
// 不谎称全做完：过不去的题永远跟着结果一起报。
assert.match(js, /skipped: data\.skipped \|\| \[\]/);
assert.ok(!js.includes("已通过所有题目"), "不许用「已通过所有题目」这种说法");

console.log("approve all green checks: OK");
