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
for (const id of ["libraryRestoreHints", "libraryKeysButton"]) {
  assert.match(libraryHtml, new RegExp(`id="${id}"[^>]*data-drawer="tools"`), `${id} 进了抽屉的工具组`);
}
assert.match(libraryHtml, /class="source-link"[^>]*data-drawer="tools"/);
assert.match(libraryHtml, /id="openDrafts"[^>]*data-drawer="library"/, "组卷草稿和专注浏览同在题库组");
assert.match(libraryHtml, /id="basketPanel"[^>]*data-drawer="basket"/);
assert.match(libraryHtml, /id="libraryShortcutHint"[^>]*data-drawer="hint"/);
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
