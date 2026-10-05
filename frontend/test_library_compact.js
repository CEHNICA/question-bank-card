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
assert.match(libraryCss, /\.basket-handle \{ --basket-fill: 0; position: fixed; right: 0; top: 50vh;/, "把手钉在视口右缘正中");
assert.ok(!libraryCss.includes(".library-workspace > .basket-handle { grid-column"), "把手是 fixed，不进栅格流");
assert.match(libraryCss, /body\.library-basket-open \.library-workspace \{ grid-template-columns: 212px minmax\(0, 1fr\) 300px; \}/, "展开时才让出第三列");

// 1.12.9 第 1 条：有题时把手点亮，越多越浓，20 题封顶。强度是 JS 写的一个 0–1 的数，
// 颜色和辉光都在样式里算 —— 别有人改回在 JS 里拼 rgba，那样 hover 就改不动了。
assert.match(libraryJs, /handle\.style\.setProperty\("--basket-fill", \(Math\.min\(1, state\.basket\.length \/ 20\)\)\.toFixed\(3\)\);/);
assert.match(libraryCss, /\.basket-handle::before \{[^}]*opacity: var\(--basket-fill\);/);
assert.match(libraryCss, /0 0 calc\(2px \+ 9px \* var\(--basket-fill\)\) rgba\(31, 107, 95, calc\(\.06 \+ \.24 \* var\(--basket-fill\)\)\);/,
  "辉光的范围和浓淡都跟着题数走，不是固定的一圈");

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
