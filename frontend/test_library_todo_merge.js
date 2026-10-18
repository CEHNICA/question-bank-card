"use strict";
// 题库页第三遍收拾（1.25.20）。起因是老师一句「我想让 AI 提一下标签的建议，
// 我发现好像没有这个按钮啊」。
//
// 量出来的事实：
//   · 勾了题之后批量行会多出「AI 建议标签」，不勾题就一个都没有 —— 所以他找不到它。
//   · 勾题之前唯一的 AI 入口，是批量行右边那个折叠着的小条，写着
//     「批量补标签和答案　要花额度　3 项」。名字说的是"补"不是"提建议"，
//     警告还占了半个名字，整屏没有一处出现过「让 AI 提标签建议」这句话。
//   · 另外两件「还有活没干」（439 题建议待采纳、805 题没打标签）挂在筛选行上，
//     和上面那个折叠条是同一件事，却分在两行、两种长相。
//
// 这版按老师选的做：筛选行只留「来源」和「更多筛选」；三样待办收成一处折叠条，
// 名字上直接把那三样数出来；「AI 建议标签」改成常驻，没勾题时灰着并说清要先勾题。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const markup = fs.readFileSync(require.resolve("./library.html"), "utf8");
const js = fs.readFileSync(require.resolve("./library.js"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");
const readingCss = fs.readFileSync(require.resolve("./library-reading.css"), "utf8");

const filterBar = markup.slice(markup.indexOf('<div id="libraryFilterBar"'), markup.indexOf('<div class="library-bulk"'));
const bulk = markup.slice(markup.indexOf('<div class="library-bulk"'), markup.indexOf('id="libraryList"'));

// ── 一、筛选行只剩两样东西 ──
assert.ok(filterBar.includes('id="sourceSelect"'), "来源还在筛选行上");
assert.ok(filterBar.includes('id="advancedFilters"'), "「更多筛选」还在筛选行上");
assert.doesNotMatch(filterBar, /libraryTodos/, "待办已经不在筛选行里了");
assert.ok(bulk.includes('id="libraryTodos"'), "待办搬到批量行上，和题目列表那一行走");
assert.doesNotMatch(markup, /id="tagQuickTools"|id="aiBatchTools"/, "旧的两个待办容器都该没了");
assert.equal(markup.split('id="libraryTodos"').length - 1, 1, "待办入口只能有一处");

// 待办绝不能住进「更多筛选」那个折叠板：它在三组筛选都为空时会整个藏起来，
// 「有 439 题建议待采纳」会跟着一起消失，有活也不吭声。
const advancedBlock = markup.slice(markup.indexOf('<details id="advancedFilters"'),
  markup.indexOf('</details>', markup.indexOf('<details id="advancedFilters"')));
assert.doesNotMatch(advancedBlock, /libraryTodos/, "待办不能住进「更多筛选」里");

// 空掉的栅格子要一起拿掉，不然「更多筛选」和来源中间会空出两道缝。
assert.match(readingCss, /grid-template-areas:\s*"source advanced"/, "筛选行的栅格只剩两列");
assert.doesNotMatch(readingCss, /grid-template-areas:[^;]*\b(quick|sort)\b[^;]*"/, "空掉的格子不能留在排版里");

// ── 二、三样待办合成一处，名字上数得出来 ──
const todos = js.slice(js.indexOf("  function renderTodos("), js.indexOf("  function currentFilterPayload("));
assert.notEqual(todos.indexOf("function renderTodos("), -1, "renderTodos was renamed or moved; update this slice");
assert.match(todos, /const menu = node\("details", "library-todo-menu"\)/, "三样待办共用一个折叠区");
assert.match(todos, /\$\{pendingCount\} 题建议待采纳/, "名字上写着有多少建议等你采纳");
assert.match(todos, /\$\{missingCount\} 题没打标签/, "名字上写着有多少题没打标签");
assert.match(todos, /AI 能补 \$\{batchActions\.length\} 项/, "名字上写着 AI 能补几项");
assert.match(todos, /"library-todo-lead", working \? "AI 正在处理" : "还有活没干"/, "入口先说自己是什么");

// 「要花额度」是警告不是动作。挤在名字里，认出来的就不是"这是干什么的"。
const summary = todos.slice(todos.indexOf("const summary = node("), todos.indexOf("menu.append(body);"));
assert.doesNotMatch(summary, /要花额度/, "「要花额度」不能占入口的名字");
assert.match(todos, /"library-todo-note", apiReady[\s\S]*?消耗额度/,
  "花费的警告挪到展开后的说明里，没有丢");

// 折叠起来的东西要让人看得见里面有什么：三样都要合成一句话，缺一样就少一件事。
assert.match(todos, /bits\.push\(`\$\{pendingCount\} 题建议待采纳`\)/);
assert.match(todos, /bits\.push\(`\$\{missingCount\} 题没打标签`\)/);
assert.match(todos, /bits\.push\(`AI 能补 \$\{batchActions\.length\} 项`\)/);
assert.match(summary, /summary\.title = working[\s\S]*?点开看这三项/, "悬停说明也要把三样说全");

// 合成一处以后，待区里混着筛选按钮。锁按钮的那一处要是用一网切的写法，
// 会把两个筛选入口一起锁掉，筛选在「已选题」视图里就再也点不动了。
assert.match(js, /querySelectorAll\("#libraryTodos \.library-tool-actions button"\)/,
  "只锁真正会排队的那几颗");
assert.match(js, /querySelectorAll\("#libraryTodos \.library-todo-look button"\)/,
  "两个筛选入口单独锁");

// ── 三、「AI 建议标签」常驻，灰着的时候说清为什么 ──
// 第一帧就要在：把正确状态写进标记本身，不靠某个时刻跑一次同步函数。
const suggestTag = markup.match(/<button[^>]*id="generateSelectedTags"[^>]*>/)[0];
assert.ok(!/\shidden\b/.test(suggestTag), "标记里就带着默认状态，第一帧不可能缺这个按钮");
assert.match(suggestTag, /title="[^"]+"/, "灰着的时候也有话说");
// 只钉标记不够：同步函数里要是又把它藏起来，标记那一处照样是对的。
assert.match(js, /const suggest = \$\("generateSelectedTags"\);[\s\S]*?suggest\.hidden = false;/,
  "每次同步也都明确把它摆出来");

// 这一条是补上才对的：只查「标记里没有 hidden」会放过去一个照样看不见的按钮。
// .bulk-actions 整组是 display:none、勾了题才显示；把按钮留在里面，标记和同步
// 都对，不勾题时页面上仍然一个入口都没有 —— 老师的原话就是这么来的。
assert.match(css, /\.bulk-actions \{ display: none;/, "勾了题才出现的那一组，默认整组不显示");
// 量这一块本身，不量整份标记：lazy 的匹配跨过别的元素去找，照样能蒙过去。
const bulkActionsAt = markup.indexOf('<div class="bulk-actions">');
const bulkActions = bulkActionsAt < 0 ? "" : markup.slice(bulkActionsAt, markup.indexOf('</div>', bulkActionsAt));
assert.ok(bulkActionsAt > -1, "找得到那组只在勾题时出现的按钮");
assert.doesNotMatch(bulkActions, /generateSelectedTags/,
  "「AI 建议标签」不能待在 .bulk-actions 里：那一组不勾题时整个不显示");
assert.match(markup, /id="suggestTagsSlot"[\s\S]*?id="generateSelectedTags"/,
  "它自己占一格，不跟着别人一起藏");
assert.match(css, /\.library-suggest-slot \{[^}]*display: flex/,
  "那一格任何时候都显示");

// 真跑一遍那两段，别只查它在不在。
const helperCode = js.slice(js.indexOf("  function suggestTagsReady("), js.indexOf("  function syncSelection("));
assert.notEqual(helperCode.indexOf("function suggestTagsTitle("), -1, "suggestTagsTitle was renamed or moved; update this slice");
const sandbox = { state: {} };
vm.runInNewContext(helperCode, sandbox);

const setState = (patch) => { Object.assign(sandbox.state, patch); };
setState({ features: { knowledge_tags: true }, ai: { mode: "api", api_ready: true }, selected: new Set() });
assert.equal(sandbox.suggestTagsReady(), true, "功能开了、API 配好了，就可以用");
assert.match(sandbox.suggestTagsTitle(), /先在左边勾几道题/, "没勾题时要说清要先勾什么");

sandbox.state.selected = new Set(["a", "b", "c"]);
assert.match(sandbox.suggestTagsTitle(), /勾选的 3 道题/, "勾了几道就写几道，不能是一句笼统的话");

setState({ features: { knowledge_tags: false } });
assert.equal(sandbox.suggestTagsReady(), false, "功能没开就不能用");
assert.match(sandbox.suggestTagsTitle(), /「设置」里打开/, "功能没开要说去哪开，而不是只说不可用");

setState({ features: { knowledge_tags: true }, ai: { mode: "assistant", api_ready: false } });
assert.equal(sandbox.suggestTagsReady(), false);
assert.match(sandbox.suggestTagsTitle(), /API 配置/, "API 没配好要说去哪儿配");

// ── 四、搬走以后留下的死代码和死样式 ──
for (const dead of ["library-batch-ai", "library-batch-cost", "library-batch-count",
  "library-extra-tools", "library-batch-toolbar", "renderExtraTools", "aiBatchTools"]) {
  assert.doesNotMatch(css + readingCss, new RegExp(dead), `${dead} 在样式里已经匹配不到任何东西了`);
  assert.doesNotMatch(js + markup, new RegExp(dead), `${dead} 在代码里已经不存在了`);
}

console.log("Pending work is one named entry, the filter row is down to two controls, and AI tag suggestions are always findable: OK");
