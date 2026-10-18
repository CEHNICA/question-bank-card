"use strict";
// 题库页收拾干净（1.25.17）。两件事，都是实机量出来的：
//   · 顶栏那个「题目详情」按钮没用 —— 和右侧面板里的「收起」是同一件事，两个入口；
//     而且宽屏上点任何一张题卡面板都会自动弹回来，按它关掉也关不住。
//   · 看到第一道题之前有三行控件、21 个。用户选了更彻底那版：题型和排序收进
//     「更多筛选」，明面只留一行；题型筛选用着的时候本来就会在下面那行显示成标签。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const markup = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

// ── 一、「题目详情」在宽屏上不该出现 ──
assert.match(markup, /id="inspectorToggle"[^>]*>题目详情</, "这个按钮还在标记里 —— 窄屏上要靠它打开浮层");
const closeTag = markup.match(/<button[^>]*id="closeInspector"[^>]*>/)[0];
assert.ok(markup.includes(`${closeTag}收起</button>`), "面板里那个「收起」必须留着：宽屏上收完就只剩这一个入口");
// 只查「有没有这个按钮」不够 —— 藏起来了一样是关不掉。漏过一条变异才发现。
assert.doesNotMatch(closeTag, /\shidden\b/, "「收起」不能藏起来，宽屏上那个按钮已经没了，只剩它能关");

// 藏它的规则必须只针对宽屏。不分屏一律藏起来，窄屏就再也打不开那块浮层了。
const wide = css.match(/@media \(min-width: 980px\) \{([\s\S]*?)\n\}/);
assert.ok(wide, "要有一条只针对宽屏的规则");
assert.match(wide[1], /\.inspector-toggle \{ display: none; \}/, "宽屏上把那个按钮藏掉");
assert.doesNotMatch(css, /^\.inspector-toggle \{ display: none; \}$/m,
  "不能不分屏一律藏 —— 窄屏上它是唯一能打开右侧浮层的入口");
assert.ok(!/max-width: 979px[^}]*inspector-toggle \{ display: none; \}/.test(css),
  "藏的方向不能反：现在是宽屏开着的窄屏才该用这个开关");

// ── 二、题型和排序落在「更多筛选」里面，明面那行只剩来源和待办 ──
const barStart = markup.indexOf('<div id="libraryFilterBar"');
const barEnd = markup.indexOf('<div class="library-bulk"', barStart);
assert.ok(barStart > -1 && barEnd > barStart, "找得到筛选条");
const bar = markup.slice(barStart, barEnd);
const advAt = bar.indexOf('<details id="advancedFilters"');
assert.ok(advAt > -1, "筛选条里有「更多筛选」");
assert.ok(bar.indexOf('id="typeFilters"') > advAt, "题型得落在「更多筛选」里面");
assert.ok(bar.indexOf('id="sortSelect"') > advAt, "排序也得落在「更多筛选」里面");
assert.ok(bar.indexOf('id="typeFilters"') === bar.lastIndexOf('id="typeFilters"'),
  "题型那块只能有一处，别在折叠板外面留个副本");

// 折叠板之前只剩「来源」——用户要的是平时只留一行，别又把东西塞回外面。
const before = bar.slice(0, advAt);
assert.match(before, /id="sourceSelect"/, "来源留在明面上");
assert.doesNotMatch(before, /typeFilters|sortSelect/, "折叠板之前只该有来源");

// ── 二之二、1.25.20：待办整块搬到批量行，筛选行只剩来源和更多筛选 ──
// 它必须还在折叠板外面：整个折叠板在筛选项都为空时会藏起来，
// 待办要是跟着进去，页面上就没人知道还有活没干了。
assert.equal(markup.split('id="libraryTodos"').length - 1, 1, "待办入口只能有一处");
assert.doesNotMatch(bar, /libraryTodos/, "待办已经不在筛选行里了");
const bulk = markup.slice(markup.indexOf('<div class="library-bulk"'), markup.indexOf('id="libraryList"'));
assert.ok(bulk.includes('id="libraryTodos"'), "待办住在批量行上");
assert.doesNotMatch(markup, /id="tagQuickTools"|id="aiBatchTools"/, "旧的两个待办容器都该没了");

// ── 三、题型搬进去不会「丢了」：筛选用着的时候它会在标签行里显示出来 ──
const chip = libraryJs.match(/"type", state\.type \? `([^`]+)`/);
assert.ok(chip, "筛选标签行里得有题型这一项");
assert.match(markup, /<div id="activeFilters"[^>]*hidden>/, "没有筛选时那行标签是藏着的");
assert.equal((libraryJs.match(/state\.type \? `题型/g) || []).length, 1,
  "题型这一项只该算一遍，别在别处又写一份");

// ── 四、搬动留下的死代码要清干净 ──
assert.doesNotMatch(css, /\.library-filterbar > \.library-facet/,
  "题型不再是指向筛选条的直接子元素，这两条覆盖规则已经匹配不到了");
assert.doesNotMatch(css, /\.library-filterbar > \.library-facet #typeFilters/,
  "同理，题型那几条贴边距的规则也失效了");
// 窄屏上原本有一条把下拉框前面的字全藏掉的规则，用的是后代选择器，会把「更多筛选」
// 里面那两个下拉也一起藏掉 —— 藏了就等于没有名字。只能藏筛选条上直接的那一个。
assert.match(css, /\.library-filterbar > \.library-select > span \{ display: none; \}/,
  "窄屏只藏来源的前字");
assert.doesNotMatch(css, /\.library-filterbar \.library-select span \{ display: none; \}/,
  "后代选择器会把「更多筛选」里那两个下拉的前字也藏掉");

console.log("The filter bar is one row on a wide screen, the redundant detail toggle is gone: OK");
