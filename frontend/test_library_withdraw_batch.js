"use strict";

// 1.12.6：题库批量撤回。两条硬要求：
// ① 一次撤一批（一份卷 25 道，逐题点要点 25 下，做不完）。
// ② 确认框必须按来源任务分组写出卷名、题数、录入时间 —— 真库里同一份卷被
//    录过两次，两条来源在题库里同名，只看文件名分不出这次撤的是哪一份。
//    撤错了没法补救，所以这一步不能省。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./library.js"), "utf8");
const html = fs.readFileSync(require.resolve("./library.html"), "utf8");
const cut = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));

// 按钮挂在批量选题条上，和「加入试题篮」同一排。
assert.match(html, /<button id="withdrawSelected"[^>]*disabled[\s\S]*?>撤回所选<\/button>/);
assert.match(html, /id="withdrawSelected"[^>]*title="把勾选的题从正式题库撤下；题卡和原卷都保留，可以重新入库"/);
// 按钮挂在批量选题条上，和「加入试题篮」同一排。1.12.7 把状态行也并进了这一行，
// 所以这段切片要罩住整条批量条（到下一块 library-load-error 为止）。
const bulk = html.slice(html.indexOf('class="library-bulk"'), html.indexOf('id="libraryLoadError"'));
for (const id of ["selectVisible", "addSelected", "withdrawSelected", "clearSelection"]) {
  assert.ok(bulk.includes(`id="${id}"`), `批量条里应有 ${id}`);
}
assert.ok(bulk.includes('id="libraryStatus"'), "状态行已经并进批量条，不再单占一行");
// 跟着勾选状态启停，和「加入试题篮」一样。
const sync = cut("  function syncSelection()", "  async function batchItems(");
assert.match(sync, /\$\("withdrawSelected"\)\.disabled = !state\.selected\.size;/);
assert.match(sync, /\$\("addSelected"\)\.disabled = !state\.selected\.size;/);

// 确认框按来源分组：卷名 + 题数 + 录入时间。
const batch = cut("  async function withdrawSelected()", "  // ---------------------------------------------------------------- 出处");
assert.match(batch, /const groups = new Map\(\);/);
assert.match(batch, /const key = item\.document_id \|\| item\.source_filename;/,
  "同名但不同任务必须分成两组");
assert.match(batch, /《\$\{group\.name\}》：\$\{group\.items\.length\} 道题，录于 \$\{shortDate\(group\.at\)\}/,
  "确认框要同时写出卷名和题数");

// 三处显示的录入时间必须取自同一处，否则用户对照下拉会以为那是两条记录。
const timeFn = cut("  function shortDate(value)", "  function fullDate(");
assert.match(timeFn, /function sourceFirstSeen\(documentId, fallback\)/);
assert.match(timeFn, /state\.facets\?\.sources \|\| \[\]/, "统一从 facets 取该来源的首次入库时间");
assert.match(cut("  function card(item)", "  function visibleItems("),
  /录于 \$\{shortDate\(sourceFirstSeen\(item\.document_id, item\.published_at\)\)\}/);
assert.match(batch, /录于 \$\{shortDate\(group\.at\)\}/);
assert.match(batch, /at: sourceFirstSeen\(item\.document_id, item\.published_at\)/);

// 来源下拉也要分得开，否则用户在下拉里就选错了。
const facets = cut("  function renderFacets()", "  function renderTagFilter(");
assert.match(facets, /const nameTotals = new Map\(\);[\s\S]*?nameTotals\.set\(source\.filename, \(nameTotals\.get\(source\.filename\) \|\| 0\) \+ 1\)/,
  "同名来源要数出来");
assert.match(facets, /nameTotals\.get\(source\.filename\) > 1 \? `，录于 \$\{shortDate\(source\.first_published_at\)\}` : ""/,
  "同名来源在下拉里补上录入时间，否则随手一选就可能撤错那一份");
assert.match(timeFn, /hour: "2-digit", minute: "2-digit"/,
  "只到天的日期仍会撞：同一天录两次照样分不出");

assert.match(batch, /题卡和原卷都保留，撤回之后重新打勾就能再入库。/,
  "撤回是可逆的，不许承诺撤不回来");
assert.match(batch, /ok: "撤回所选", danger: true/);

// 一次请求撤完，不循环单题端点。
assert.match(batch, /await fetch\("\/api\/library\/withdraw-batch"/);
assert.doesNotMatch(batch, /`\/api\/library\/\$\{encodeURIComponent\(item\.id\)\}\/withdraw`/);
assert.match(batch, /body: JSON\.stringify\(\{ ids: picked\.map\(\(item\) => item\.id\) \}\)/);

// 撤不成的逐条报出来，不静默吞。
assert.match(batch, /const skippedText = skipped\.length[\s\S]*?\$\{skipped\.length\} 道没撤成（\$\{\[\.\.\.new Set\(skipped\.map\(\(row\) => row\.reason\)\)\]\.join\("、"\)\}）/);
assert.match(batch, /withdrawn\.has\(item\.id\)/);

// 题卡上也要能分清同名来源：录过两次的卷补上录入时间。
assert.match(source, /function duplicateSourceNames\(\) \{[\s\S]*?count > 1/);
assert.match(cut("  function card(item)", "  function visibleItems("),
  /duplicateSourceNames\(\)\.has\(item\.source_filename\)[\s\S]*?录于 /);
assert.match(cut("  function card(item)", "  function visibleItems("),
  /when\.title = `这份来源的入库时间：\$\{fullDate\(sourceFirstSeen\(item\.document_id, item\.published_at\)\)\}`/);

// 只有一个来源时不加多余的一行。
assert.doesNotMatch(cut("  function duplicateSourceNames()", "  function card(item)"),
  /if \(.*count === 1\)/);

console.log("Library withdraw batch: one request per batch, per-source grouping with names, counts and dates, every skip reported, reversible wording: OK");
