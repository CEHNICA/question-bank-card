"use strict";
// 题库页第三遍收拾（1.25.20）+ 第四遍（1.25.21）。
//
// 1.25.20 的起因是老师一句「我想让 AI 提一下标签的建议，我发现好像没有这个按钮啊」。
// 量出来的事实：
//   · 勾了题之后批量行会多出「AI 建议标签」，不勾题就一个都没有 —— 所以他找不到它。
//   · 勾题之前唯一的 AI 入口，是批量行右边那个折叠着的小条，写着
//     「批量补标签和答案　要花额度　3 项」。名字说的是"补"不是"提建议"，
//     警告还占了半个名字，整屏没有一处出现过「让 AI 提标签建议」这句话。
//   · 另外两件「还有活没干」（439 题等你核对、805 题没知识点）挂在筛选行上，
//     和上面那个折叠条是同一件事，却分在两行、两种长相。
//
// 1.25.21 的起因是老师一句「我总感觉还是有点问题」，量出来的：
//   · AI 一登记，两个"不花钱的筛选"跟着变灰 —— 两个循环按不同的判据锁同一批按钮，
//     后写的那个赢。上一版的测试只断言"单独锁筛选的那行代码存在"，所以放过了它。
//   · 面板里五颗按钮连边框颜色都一样，「只看」和「让 AI 去干」只差一个「+」号。
//   · 折叠条写「建议待采纳」、按钮写「待核对」；折叠条写「没打标签」、按钮写「未标知识点」。
//     第三样「AI 能补 3 项」说的是按钮数，不是活的数量。
//   · 筛选行第一列是 1fr 撑到 1028px，下拉只占 230px，中间空 777px；
//     网格模板里还留着一个从来没有东西落进去的 types 行，白占 15px 行间距。
//   · 「只处理当前这批」不筛时是 807 次 AI 调用，说明里却只写"每道题调用一次"。
//
// 有两处查过之后决定不改：
//   · 「未标知识点」不是用词不一致，是准确 —— 标签分知识点/解题方法/自定义/历史四类，
//     这个筛选只认知识点那一类，一道题打了「解题方法」没打「知识点」仍算它没打。
//     改成「未打标签」反而会撒谎。
//   · 右侧试题篮那个图标放大看是提手篮子，不是垃圾桶。
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
// 「有 439 题等你核对」会跟着一起消失，有活也不吭声。
const advancedBlock = markup.slice(markup.indexOf('<details id="advancedFilters"'),
  markup.indexOf('</details>', markup.indexOf('<details id="advancedFilters"')));
assert.doesNotMatch(advancedBlock, /libraryTodos/, "待办不能住进「更多筛选」里");

// 1.25.21：两个控件挨在一起靠左，不是一头一尾。
// 原来第一列是 1fr，被撑到 1028px，下拉只占 230px，中间空 777px，看着像少了东西。
// 量这一条规则本身，别只查"没有 quick/sort"——那是上一版的检查，它对 1fr 一点办法没有。
//
// 两条都要钉。第一版只把 1fr 换成 auto，测试也是绿的，装上实测两个控件之间仍有
// 395px —— 因为 auto 轨道的最大值虽然是 max-content，可它同时是能被
// justify-content: stretch 撑大的那种轨道。只量列宽那一半，会把这个坑放过去。
const filterbarRule = readingCss.match(/\.library-page \.library-filterbar \{[^}]*\}/);
assert.ok(filterbarRule, "找得到筛选行那条栅格规则；改版时 update this slice");
assert.match(filterbarRule[0], /grid-template-columns:\s*max-content max-content/,
  "两列都按内容的实际宽度，不吃剩余空间");
assert.match(filterbarRule[0], /justify-content:\s*start/,
  "轨道不拉伸还不够，内容还得贴到行首");
assert.doesNotMatch(filterbarRule[0], /grid-template-areas/,
  "命名区域已经用不上了");
for (const dead of ["quick", "sort", "types"]) {
  assert.doesNotMatch(readingCss, new RegExp(`grid-template-areas:[^;]*\\b${dead}\\b[^;]*"`),
    `空掉的 ${dead} 格子不能留在排版里`);
}
// 那个 types 行是道幽灵行：题型是 .library-filters 的子节点，不是筛选栏这一层的
// 子节点，所以从来没有东西落进去，可行高照样算 —— 筛选行下面因此永远空 15px。
assert.doesNotMatch(readingCss, /\.library-filterbar > \.library-facet \{[^}]*grid-area/,
  "不能再给一个不在这层的元素留格子，那格子留一次就空一次 15px");
// 窄屏那条从 1.25.17 起也只有两个控件了，第三列从那时起就没有东西落进去。
// 空列在窄屏撑不出高度，所以它不显形 —— 属于同一类留到没人记得的排版残留。
assert.doesNotMatch(readingCss, /\.library-filterbar \{[^}]*minmax\(0, 1fr\) auto auto/,
  "窄屏也不能留第三列");

// ── 二、三样待办合成一处，名字上数得出来 ──
const todos = js.slice(js.indexOf("  function renderTodos("), js.indexOf("  function currentFilterPayload("));
assert.notEqual(todos.indexOf("function renderTodos("), -1, "renderTodos was renamed or moved; update this slice");
assert.match(todos, /const menu = node\("details", "library-todo-menu"\)/, "三样待办共用一个折叠区");
// 1.25.21：折叠条上写的话必须和展开后按钮上的话一字不差。
// 原来折叠条写「建议待采纳」「没打标签」，按钮写「AI 建议待核对」「未标知识点」——
// 同一件事、隔着 30 像素两个名字，老师点开才发现对不上。
assert.match(todos, /\$\{pendingCount\} 题等你核对/, "折叠条上写有多少题等你核对");
assert.match(todos, /AI 建议待核对 · \$\{pendingCount\} 题/, "按钮上说的也是等你核对");
assert.match(todos, /\$\{missingCount\} 题没知识点/, "折叠条上写有多少题没知识点");
assert.match(todos, /未标知识点 · \$\{missingCount\} 题/, "按钮上说的也是知识点");
assert.match(todos, /"library-todo-lead", working \? "AI 正在处理" : "还有活没干"/, "入口先说自己是什么");

// 第三样原来是「AI 能补 3 项」—— 说的是下面有几颗按钮，不是有多少活干。换成真数字。
// 锚在 bits.push( 上，别用裸词：这段代码里正好有一句注释解释为什么删掉它，
// 裸词会把那句解释一起判掉，然后下一个人为了让测试变绿把注释删了。
assert.doesNotMatch(todos, /bits\.push\(`AI 能补/, "不能用「下面有几颗按钮」冒充「还有多少活」");
assert.match(todos, /\$\{answerCount\} 题没答案/, "折叠条上写出还没答案的真数字");

// 「要花额度」是警告不是动作。挤在名字里，认出来的就不是"这是干什么的"；
// 但它也不能丢 —— 挪到看得见的组标题上，不是挪进悬停里等人发现。
const summary = todos.slice(todos.indexOf("const summary = node("), todos.indexOf("menu.append(body);"));
assert.doesNotMatch(summary, /要花额度/, "「要花额度」不能占入口的名字");
assert.match(todos, /library-todo-head-cost", apiReady[\s\S]*?让 AI 逐题处理 · 每题消耗一次额度/,
  "花费的警告提成看得见的组标题，跟着按钮走，没有丢");

// 折叠起来的东西要让人看得见里面有什么：三样都要合成一句话，缺一样就少一件事。
assert.match(todos, /bits\.push\(`\$\{pendingCount\} 题等你核对`\)/);
assert.match(todos, /bits\.push\(`\$\{missingCount\} 题没知识点`\)/);
assert.match(todos, /bits\.push\(`\$\{answerCount\} 题没答案`\)/);
assert.match(summary, /summary\.title = working[\s\S]*?点开看：/, "悬停说明也要把三样说全");
assert.match(summary, /上面几项点一下只看这些题、不花钱；下面几项会让 AI 逐题处理、消耗额度/,
  "悬停里说清哪几项不花钱");

// 1.25.21（本版最重要的一条）：五颗按钮原来连边框都一样，唯一区别是 AI 那三颗
// 前面多个「+」号 —— 最该多点两下的看着最不像按钮，最该慎的最像能随手点。
// 现在靠两样东西分开：看得见的组标题，和付费那三颗的实线边框。
assert.match(todos, /library-todo-head-free", "只看这些题 · 不花钱"/, "免费的自己有一个看得见的组标题");
assert.match(css, /\.library-todo-look \.library-tool-action \{ border-color: transparent; \}/,
  "筛选那两颗保持无边框的纯文字，和题型筛选同一套长相");
assert.match(css, /\.library-todo-body > \.library-tool-actions > \.library-tool-action \{ border-color: var\(--line-strong\); \}/,
  "要花钱的那三颗给一道实线边框，不能和免费的只差一个「+」");

// ── 二之二、免费的两个筛选不能被"AI 正在排队"锁死 ──
// 这一条上一版就写错了：当时只断言"那行单独锁筛选的代码存在"，可它后面紧接着又调
// applyBatchButtonState，后者把两个筛选重新按 queueBusy 锁了一遍 —— 后写的赢，
// AI 一登记它们就跟着灰。断言通过，毛病照旧。根子在于 applyBatchButtonState 是按
// .library-tool-actions 这个 class 圈按钮的，所以它圈没圈到筛选，只看筛选组挂没挂
// 那个 class。这条就钉它本身。
const lookContainer = todos.match(/const look = node\("div", "([^"]+)"\)/);
assert.ok(lookContainer, "找得到筛选组的容器；改版时 update this slice");
assert.ok(!lookContainer[1].trim().split(/\s+/).includes("library-tool-actions"),
  "筛选组不能挂 library-tool-actions：applyBatchButtonState 就是按这个 class 圈按钮的，"
  + "挂了它，AI 一登记两个免费筛选就跟着变灰");
assert.match(js, /querySelectorAll\("#libraryTodos \.library-tool-actions button"\)/,
  "锁 AI 动作的那句选择器没变");
assert.match(js, /querySelectorAll\("#libraryTodos \.library-todo-look button"\)/,
  "锁筛选的那句选择器也还在——现在两处管的是不相交的两组按钮");
// 顺序也重要：先把筛选按"只看当前视图"锁好，再调 applyBatchButtonState。
// 两次调用之间不能夹着另一次 applyBatchButtonState —— 后写的会赢，净效果又变成
// "AI 排队时筛选也灰"。这里量的是两次调用之间的那一段，不靠猜 offset。
const lookAt = js.indexOf('querySelectorAll("#libraryTodos .library-todo-look button")');
const applyAt = js.indexOf("applyBatchButtonState();", lookAt);
assert.ok(lookAt > -1 && applyAt > lookAt, "两次调用都在，找得到");
assert.ok(!js.slice(Math.max(0, lookAt - 400), applyAt).includes("applyBatchButtonState();"),
  "先锁筛选、再交给 applyBatchButtonState，中间不能夹第二次调用");

// ── 三、「AI 建议标签」常驻，灰着的时候说清为什么 ──

// 1.25.21 顺手修的一个陷阱：「只处理当前这批」不筛任何东西时会一次排 807 道题，
// 也就是 807 次 AI 调用，可它的说明只写「每道题调用一次 AI，会消耗额度」——
// 代价没写成数字，看着像个小动作。次数必须直接说出来，悬停和确认框两处都要。
assert.match(todos, /点下去会调用 AI \$\{Number\(state\.total\) \|\| 0\} 次/,
  "悬停里把这次会调用多少次 AI 写成数字");
const currentHandler = todos.slice(todos.indexOf('const current = iconButton("button"'),
  todos.indexOf("batchActions.push(current);"));
assert.match(currentHandler, /current\.title = apiReady[\s\S]*?会调用 AI/,
  "代价写在这一颗按钮自己的说明里");
const currentConfirm = todos.slice(currentHandler.indexOf("confirmDialog("));
assert.match(currentConfirm, /这一次会调用 AI \$\{count\} 次/,
  "按下确认框那一句也要有次数——那是最后一次拦下误点的机会");

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

console.log("Pending work is one named entry, the filter row is down to two controls, "
  + "AI tag suggestions are always findable, the free filters survive an AI run, "
  + "and spending money looks different from just looking: OK");
