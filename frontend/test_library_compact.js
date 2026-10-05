"use strict";

/*
 * 1.12.7 题库版面重排（静态回归）。唯一目标：顶到第一道题之前的附属横条越少越好。
 * 这些断言盯的是「别把省下来的高度又还回去」——
 * 有人把某一栏改回独立一行，题面就少一屏，测试要立刻红。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");

const libraryHtml = fs.readFileSync(require.resolve("./library.html"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");
const stylesCss = fs.readFileSync(require.resolve("./styles.css"), "utf8");
const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");

// ---- 不再有的东西
assert.ok(!libraryHtml.includes('class="library-results-head"'), "范围切换那一行已并进筛选行");
assert.ok(!libraryHtml.includes('id="basketToggle"'), "试题篮开关进了抽屉，顶栏筛选行不再留它");
assert.ok(!libraryJs.includes('$("basketToggle")'), "旧的篮子开关监听必须一起删干净");
assert.ok(!libraryCss.includes("basket-collapsed"), "篮子不再占栅格右列");
assert.ok(!libraryCss.includes("library-results-head"), "专注模式的样式不再针对那一行");
assert.ok(!libraryJs.includes("--basket-top"), "篮子不吸顶了，不必再算这个变量");

// ---- 导航与工具折进抽屉，但类名一个字没改
assert.match(libraryHtml, /<nav class="topnav header-nav"[^>]*data-drawer="nav">/);
assert.match(libraryHtml, /<a class="header-link" href="\/">录入终审<\/a>/, "导航字面保留，教学巡演和未保存拦截都按它定位");
assert.match(libraryHtml, /id="libraryKeysButton"[^>]*data-drawer="tools"/, "快捷键按钮留在抽屉的工具组");
// 1.12.7：抽屉里三样东西删了 —— 源码链接、整个「操作说明」组、还有「恢复操作提示」。
// 源码在设置 → 关于里（AGPL 义务由那一栏和发布页承担），操作说明在设置里，
// 顶到第一道题之前的横条能少一条是一条。
assert.ok(!libraryHtml.includes("libraryRestoreHints"), "题库页不再挂「恢复操作提示」按钮");
assert.ok(!libraryHtml.includes('class="source-link"'), "抽屉里不再有源码链接");
assert.ok(!fs.readFileSync(require.resolve("./index.html"), "utf8").includes('class="source-link"'), "录入终审那一页的抽屉里也不再是源码");
assert.ok(!stylesCss.includes(".source-link"), "相关的样式一起删干净");
const drawerJs2 = fs.readFileSync(require.resolve("./site-drawer.js"), "utf8");
assert.ok(!/name:\s*"hint"/.test(drawerJs2), "抽屉不再有「操作说明」组");
assert.ok(!stylesCss.includes(".library-shortcut-hint"), "抽屉里那条提示的样式不再需要");
assert.ok(!libraryHtml.includes('id="libraryShortcutHint"') && !libraryHtml.includes('id="printShortcutHint"'),
  "题库页不再常驻两条快捷键提示条");
assert.ok(!libraryJs.includes("mountHint"), "提示条搬去了设置，题库页一个都不挂");
assert.ok(!libraryJs.includes("syncRestoreHints"), "没有在这里关掉的提示，就没有「恢复」要同步");
assert.match(libraryHtml, /id="openDrafts"[^>]*data-drawer="library"/, "组卷草稿和专注浏览同在题库组");
// 1.12.7：篮回到题库页自己身上 —— 右边缘一条常驻把手 + 一个抽屉面板。
// 顶栏那两个按钮撤掉了，导航抽屉（☰）里也不再有一份。
assert.match(libraryHtml, /id="basketHandle"[^>]*class="basket-handle"|class="basket-handle"[^>]*id="basketHandle"/, "右边缘有一条常驻把手");
assert.match(libraryHtml, /id="basketPanel"[^>]*class="basket-panel"(?![^>]*data-drawer)/, "篮面板在工作区里，不再搬进抽屉");
assert.ok(!libraryHtml.includes('data-drawer="basket"'), "抽屉里不再有试题篮");
assert.ok(!libraryHtml.includes('id="basketPreviewShortcut"'), "组卷预览只在篮里，没有第二个家");
// 把手改成悬浮：它骑在视口右缘，不占栅格列，收起时那 26px 全还给题目。
assert.match(libraryCss, /\.basket-handle \{ position: fixed; right: 0; top: 50vh;/, "把手钉在视口右缘正中");
assert.ok(!libraryCss.includes(".library-workspace > .basket-handle { grid-column"), "把手是 fixed，不进栅格流");
assert.match(libraryCss, /body\.library-basket-open \.library-workspace \{ grid-template-columns: 212px minmax\(0, 1fr\) 300px; \}/, "展开时才让出第三列");

// 1.13.4：「越绿越亮」那套浓度撤了。实测篮里 1 道题时把手和空篮的最大色差只有
// 2/255、5 道题 7/255，真实使用区间里等于没有；有题没看改成荧光呼吸灯。
assert.doesNotMatch(libraryCss, /--basket-fill/, "题数浓度已撤，别再加回来");
assert.doesNotMatch(libraryJs, /--basket-fill/);
assert.doesNotMatch(libraryCss, /\.basket-handle::before/, "半透明墨绿底一起撤了");
// 1.13.4 荧光呼吸灯：只有「有题 + 没展开 + 没看过」三个条件同时成立才亮。
assert.match(libraryCss,
  /body\.library-basket-has-items:not\(\.library-basket-open\):not\(\.library-basket-seen\) \.basket-handle::after \{[^}]*animation: basket-handle-fluoresce 2\.8s ease-in-out infinite;/,
  "荧光的三个条件缺一不可");
const fluoresce = libraryCss.match(/@keyframes basket-handle-fluoresce \{[\s\S]*?\n\}/);
assert.ok(fluoresce, "荧光关键帧在");
assert.match(fluoresce[0], /background-color: var\(--glow-off\)/);
assert.match(fluoresce[0], /background-color: var\(--glow-on\)/);
assert.match(fluoresce[0], /box-shadow: -8px 0 15px/, "波峰的内层溢光往左偏");
assert.match(fluoresce[0], /-14px 0 28px/, "波峰的外层柔晕也往左偏");
// 溢光被视口切掉一半会露硬边：把手右边缘到视口实测是 0，所以每层的 x 偏移必须
// 不小于该层模糊半径的一半，让右边界落在视口上、强度已衰减到 0。
for (const layer of fluoresce[0].matchAll(/(-?\d+)px 0 (\d+)px/g)) {
  assert.ok(Math.abs(Number(layer[1])) >= Number(layer[2]) / 2,
    `溢光层 ${layer[1]}px/${layer[2]}px 的右边界会被视口切掉`);
}
assert.match(libraryCss, /@media \(prefers-reduced-motion: reduce\) \{ \.basket-handle::after \{ animation: none !important; \} \}/,
  "减少动态效果时必须写 animation: none 且要 !important：只压 duration 的话 infinite 动画仍会转，不加 !important 又会被上面那条三类的状态选择器盖住");
assert.match(libraryJs, /document\.body\.classList\.toggle\("library-basket-seen", basketHandleSeen\(\)\);/,
  "看过一次之后不再闪，标志跟着每次渲染同步");
assert.match(libraryJs, /if \(next\) \{ setRail\(false\); if \(!basketHandleSeen\(\)\) markBasketHandleSeen\(\); \}/,
  "展开篮子那一刻记下「已经看过」");

// 1.12.9 第 2 条：输出内容默认只出题目。页面初始值、代码兜底、服务端草稿默认三处
// 必须一致，只改一处就会出现「界面默认题目、下次开草稿又变回题目＋答案」。
assert.match(libraryHtml, /<option value="questions" selected>题目<\/option>/, "页面初始值落在「题目」");
assert.ok(!libraryHtml.includes('<option value="combined" selected>'), "旧的 selected 已经从「题目＋答案」上摘掉");
assert.match(libraryJs, /: "answers" in options \? \(options\.answers \? "combined" : "questions"\) : "questions";/,
  "兜底默认是「题目」，但老草稿只有 answers 时的推法必须和服务端一致");

// 1.12.9 第 3 条：专注模式 + 篮展开时题面曾经只剩 309px。两条规则打架过一次，
// 这里钉死，别有人为了「统一」把这两条删掉。
assert.match(libraryCss, /\.library-focus-mode\.library-basket-open \.library-workspace \{ grid-template-columns: minmax\(0, 1fr\) 300px; \}/,
  "专注模式下篮子只占第二列");
assert.match(libraryCss, /\.library-focus-mode \.library-workspace > \.basket-panel \{ grid-column: 2; \}/,
  "篮子面板跟着挪到第二列");
for (const page of ["index.html", "library.html"]) {
  const html = fs.readFileSync(require.resolve(`./${page}`), "utf8");
  assert.match(html, /<script src="\/site-drawer\.js" defer><\/script>/, `${page} 要加载抽屉`);
}

// 1.13：「打开文件 / 打开文件夹」原来调的是本文件里根本不存在的 api()，一点就抛
// ReferenceError，弹出来的是英文的 "api is not defined"，看着像「没反应」。
// 后端接口是好的、98 个前端测试和 1383 个后端测试也全绿 —— 因为没有一个测试点过它。
// 静态这一条能挡住「再写一个不存在的函数」，真的点一次靠 tools/check_1127c_stress.py。
// 剥掉行注释和块注释再扫：不然「为什么不能调 api()」这句解释本身就会把自己判成有罪。
const libraryJsCode = libraryJs
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");
assert.match(libraryJs, /async function openExported\(target, fileToken\)/);
assert.match(libraryJs, /fetch\("\/api\/export-preferences\/open", \{\s*method: "POST", headers: \{ "Content-Type": "application\/json", "X-QB-Request": "1" \}/,
  "打开导出位置必须走 fetch 并带上这两个头，少一个会被 _guard 挡成 403");
assert.ok(!/(?<![.\w$])api\s*\(/.test(libraryJsCode), "代码里没有 api 这个函数，别再调它");

// 1.13：「更多筛选」一展开就冒出横向滚动条。两条都要钉：只钉 white-space: normal，
// 下一个长标签还会把这一栏顶宽。
assert.match(libraryCss, /\.library-extra-tools \.button \{ white-space: normal; \}/);
assert.match(libraryCss, /\.library-rail > \* \{ min-width: 0; \}/);
// 只盯侧栏自己那一处：文件里本来就有几处正当的 overflow-x: hidden
// （题干、编辑器列表横向滚动），不能一刀切。判之前先剥注释 ——
// 「故意不写 overflow-x: hidden」这句解释本身也会被一刀切误伤。
const libraryCssCode = libraryCss.replace(/\/\*[\s\S]*?\*\//g, " ");
const railRule = libraryCssCode.slice(libraryCssCode.indexOf(".library-rail {"), libraryCssCode.indexOf(".library-rail .library-filters"));
assert.ok(!railRule.includes("overflow-x: hidden"),
  "不能靠 hidden 遮掉筛选栏的溢出：那会让 scrollWidth == clientWidth 的断言永远为真，回归测试就瞎了");

// 1.12.10：批量条吸顶。四条一起钉 —— 少钉任何一条，下一轮都会退化回去。
assert.match(libraryCss, /\.library-bulk \{[^}]*position: sticky; top: var\(--topbar-h\); z-index: 20;/);
assert.match(libraryCss, /\.library-bulk\.stuck \{[^}]*box-shadow:/);
// 跳题的预留必须跟着条的真实高度走，不能写死 —— 窄屏上条会排成两行。
assert.match(libraryCss, /\.library-card \{[^}]*scroll-margin-top: calc\(var\(--topbar-h\) \+ var\(--library-bulk-h, 0px\) \+ 12px\);/);
assert.ok(!libraryCss.includes(".library-focus-mode .library-card { scroll-margin-top"),
  "专注模式那条是重复的：条隐藏时 --library-bulk-h 已经是 0");
assert.match(libraryJs, /setProperty\("--library-bulk-h"/);
assert.match(libraryJs, /ResizeObserver/);
assert.match(libraryJs, /bulkBar\.classList\.toggle\("stuck"/);
// 窄屏收状态文字
assert.match(libraryCss, /@media \(max-width: 760px\) \{[\s\S]*?\.library-bulk #libraryStatus \{ display: none; \}/);

// ---- 状态行并进批量条，元素和 aria 属性原样保留
const bulk = libraryHtml.slice(libraryHtml.indexOf('class="library-bulk"'), libraryHtml.indexOf('id="libraryLoadError"'));
for (const id of ["selectVisible", "selectionCount", "addSelected", "withdrawSelected", "clearSelection", "libraryStatus"]) {
  assert.ok(bulk.includes(`id="${id}"`), `批量条里应有 ${id}`);
}
assert.match(bulk, /id="libraryStatus"[^>]*role="status"[^>]*aria-live="polite"/, "状态行还得是会播报的 status，不能为了塞进一行就丢掉");
for (const id of ["addSelected", "withdrawSelected", "clearSelection", "generateSelectedTags", "generateSelectedAnswers"]) {
  assert.ok(bulk.slice(bulk.indexOf('class="bulk-actions"')).includes(`id="${id}"`), `${id} 归到批量按钮组`);
}
assert.match(libraryCss, /\.library-bulk\.has-selection \.bulk-actions \{ display: flex; \}/, "全选入口常驻，三个批量按钮勾了才出现");
assert.match(libraryJs, /classList\.toggle\("has-selection", state\.selected\.size > 0\)/, "勾选状态要真的驱动那一行");

// ---- 顶栏只留 ☰ 和品牌，附属横条真的变矮
assert.match(stylesCss, /--topbar-h: 48px;/);
assert.match(stylesCss, /\.topbar \{[^}]*grid-template-columns: auto minmax\(0, 1fr\) auto/);
assert.match(stylesCss, /\.topbar-tools:not\(:has\(> :not\(\[hidden\]\)\)\) \{ display: none; \}/, "顶栏工具区空着就不占位；专注浏览的退出按钮进去后自然恢复");
assert.match(libraryCss, /\.library-search input \{[^}]*min-height: 34px/, "搜索框和同一行的下拉同高，不再是整行最重的一个");
assert.match(libraryCss, /\.library-search input \{[^}]*box-shadow: none/, "阴影去掉");
assert.match(libraryCss, /\.library-search input \{[^}]*border: 1px solid var\(--line\);/);
assert.match(libraryCss, /\.library-search input:focus \{[^}]*box-shadow: 0 0 0 3px/);
assert.match(libraryCss, /\.library-workspace \{[^}]*grid-template-columns: 212px minmax\(0, 1fr\)/, "查找和筛选在左侧栏，题目拿走剩下的");
assert.match(libraryCss, /\.rail-collapse \{[^}]*position: sticky/, "收侧栏的小按钮钉在题面上，滚到哪儿都在");
assert.ok(!libraryJs.includes("QBSiteDrawer?.slot?.(\"library\")"), "抽屉里不再放一份专注浏览，只有侧栏接缝上那一个按钮");
assert.ok(libraryHtml.indexOf('id="allQuestionsButton"') < libraryHtml.indexOf('id="sourceSelect"'),
  "「全部题目 / 已选题目」并进了筛选行最左侧");
assert.ok(libraryHtml.indexOf('id="libraryRail"') < libraryHtml.indexOf('id="activeFilters"'),
  "当前筛选那行搬进了侧栏");
assert.ok(libraryHtml.indexOf('id="activeFilters"') < libraryHtml.indexOf('id="libraryList"'),
  "当前筛选那行不再夹在批量条和题目之间");
assert.ok(libraryHtml.includes('id="libraryRailScrim"') && libraryHtml.includes('id="libraryFilterToggle"'),
  "窄屏浮层要有遮罩和触发按钮");
assert.match(stylesCss, /\.site-drawer, \.drawer-scrim, \.drawer-staging \{ display: none !important; \}/);
assert.ok(libraryCss.slice(libraryCss.indexOf("@media print {")).includes(".site-drawer, .drawer-scrim, .drawer-staging"),
  "打印时抽屉和遮罩都不许出现");
assert.ok(libraryCss.slice(libraryCss.indexOf("@media print {")).includes(".library-rail, .rail-scrim"),
  "打印时侧栏和它的遮罩都不许出现");

// ---- 侧栏：三处钉死，别悄悄回退
// ① ≤1100px 那条 250px 空列（上一轮试题篮留下的，篮已经进抽屉了）不能再回来：
//    它让 980–1100px 之间的题目右边白空 264px。
const under1100 = libraryCss.slice(libraryCss.indexOf("@media (max-width: 1100px)"), libraryCss.indexOf("@media (max-width: 979px)"));
assert.ok(!under1100.includes("grid-template-columns"), "试题篮进抽屉后还给它留着一列宽度");
assert.match(libraryCss, /@media \(max-width: 979px\) \{[\s\S]*?body\.rail-open \.library-rail \{ transform: none; \}/,
  "窄屏侧栏要从右侧滑出，靠 body.rail-open 打开");
assert.match(libraryCss, /\.library-rail \{ position: sticky;/, "宽屏侧栏吸顶");
assert.match(libraryCss, /\.library-focus-mode \.library-workspace \{ grid-template-columns: minmax\(0, 1fr\)/,
  "专注浏览藏了侧栏，栅格要收回单列，否则题目被塞进空列");
assert.ok(!libraryJs.includes("syncToolbar") && !libraryJs.includes("--library-toolbar-h"),
  "顶部横条没了，吸顶高度那一套要删干净");
assert.ok(!libraryCss.includes(".library-toolbar"), "library-toolbar 已整体改成 library-rail");

// ---- 抽屉的 Esc 不能抢原生对话框的 Esc
const drawerJs = fs.readFileSync(require.resolve("./site-drawer.js"), "utf8");
assert.match(drawerJs, /event\.key === "Escape" && ui\.doc\.querySelector\?\.\("dialog\[open\]"\)/);

console.log("Library layout: results-head folded in, basket and status ride the bulk row, nav tucked into the drawer: OK");
