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
assert.match(libraryHtml, /<a class="header-link" href="\/">[\s\S]*?核对题目<\/a>/,
  "入口改成老师读得懂的名字：核对题目（原「录入终审」）；教学巡演按 href 定位，不受影响");
// 1.25.29：抽屉里只留「去哪一页」。组卷草稿搬进试题篮，快捷键交给设置 → 帮助和「?」键。
assert.doesNotMatch(libraryHtml, /id="openDrafts"[^>]*data-drawer/,
  "组卷草稿不再挂在抽屉里");
assert.match(libraryHtml, /basket-panel-head[\s\S]*?id="openDrafts"/,
  "它搬到了试题篮标题旁：选题、组卷、接着上次的草稿是同一件事");
assert.ok(!libraryHtml.includes("libraryKeysButton"),
  "抽屉里不再有第二颗快捷键入口（设置 → 帮助、题库页按「?」都能打开）");
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
assert.ok(!libraryHtml.includes("drawer-staging"), "抽屉的搬运暂存位空了，整块删掉");
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
// 1.25.20：那条规则原来挂在 .library-extra-tools 上，待办搬走时一起失效了。
// 同一件事现在由 .library-tool-action 负责（待办区里那几颗按钮用的就是它），
// 断言跟着换主人，不跟着消失。
assert.match(libraryCss, /\.library-tool-action \{[^}]*white-space: normal;/);
assert.doesNotMatch(libraryCss, /\.library-extra-tools/,
  "待办搬去批量行以后，这条挂错了主人的规则不该留在原地");
assert.match(libraryCss, /\.library-rail > \* \{ min-width: 0; \}/);
// 只盯侧栏自己那一处：文件里本来就有几处正当的 overflow-x: hidden
// （题干、编辑器列表横向滚动），不能一刀切。判之前先剥注释 ——
// 「故意不写 overflow-x: hidden」这句解释本身也会被一刀切误伤。
const libraryCssCode = libraryCss.replace(/\/\*[\s\S]*?\*\//g, " ");
const railRule = libraryCssCode.slice(libraryCssCode.indexOf(".library-rail {"), libraryCssCode.indexOf(".library-rail .library-filters"));
assert.ok(!railRule.includes("overflow-x: hidden"),
  "不能靠 hidden 遮掉筛选栏的溢出：那会让 scrollWidth == clientWidth 的断言永远为真，回归测试就瞎了");

// 1.12.10：批量条吸顶。1.25.33：只有批量条吸，筛选行留在最上面跟着页面滚走；
// 想换筛选用右下角的「回到顶部」，不再在滚动时挂一个「筛选」入口。
assert.match(libraryCss, /\.library-bulk \{[^}]*position: sticky; top: var\(--topbar-h\); z-index: 20;/,
  "批量条吸在顶栏下面");
assert.doesNotMatch(libraryCss, /\.library-sticky-tools/,
  "合成整块吸顶的那一层已经撤掉");
assert.match(libraryCss, /\.library-bulk\.stuck \{[^}]*box-shadow:/);
// 跳题的预留必须跟着条的真实高度走，不能写死 —— 窄屏上条会排成两行。
assert.match(libraryCss, /\.library-card \{[^}]*scroll-margin-top: calc\(var\(--topbar-h\) \+ var\(--library-bulk-h, 0px\) \+ 12px\);/);
assert.ok(!libraryCss.includes(".library-focus-mode .library-card { scroll-margin-top"),
  "专注模式那条是重复的：条隐藏时 --library-bulk-h 已经是 0");
assert.match(libraryJs, /setProperty\("--library-bulk-h"/);
assert.match(libraryJs, /ResizeObserver/);
assert.match(libraryJs, /bulkBar\.classList\.toggle\("stuck"/);
// 窄屏收状态文字
// 窄屏只收日常计数（data-tone="plain"），警告必须留着：整条 display:none 会让
// 手机上的用户在自己都察觉不到的情况下看着旧数据。
assert.match(libraryCss, /@media \(max-width: 760px\) \{[\s\S]*?\.library-bulk #libraryStatus\[data-tone="plain"\] \{ display: none; \}/);
assert.doesNotMatch(libraryCss, /\.library-bulk #libraryStatus \{ display: none; \}/,
  "不能整条隐藏状态行：那会把「还没应用这次筛选」也一起藏掉");
assert.match(libraryHtml, /id="libraryStatus"[^>]*data-tone="plain"/, "初始状态属于日常计数那一类");
assert.match(libraryJs, /function setStatus\(text, tone = "plain"\)/);
assert.match(libraryJs, /setStatus\(state\.items\.length \? "当前显示上一次读取的结果，尚未应用这次筛选。"/,
  "读失败/旧数据要走 setStatus");
assert.match(libraryJs, /题库尚未读取成功。", "warn"/, "读失败要标成 warn，不能在窄屏被收掉");

// ---- 状态行并进批量条，元素和 aria 属性原样保留
const bulk = libraryHtml.slice(libraryHtml.indexOf('class="library-bulk"'), libraryHtml.indexOf('id="libraryLoadError"'));
for (const id of ["selectVisible", "selectionCount", "addSelected", "withdrawSelected", "clearSelection", "libraryStatus"]) {
  assert.ok(bulk.includes(`id="${id}"`), `批量条里应有 ${id}`);
}
assert.match(bulk, /id="libraryStatus"[^>]*role="status"[^>]*aria-live="polite"/, "状态行还得是会播报的 status，不能为了塞进一行就丢掉");
for (const id of ["addSelected", "withdrawSelected", "clearSelection", "generateSelectedAnswers"]) {
  assert.ok(bulk.slice(bulk.indexOf('class="bulk-actions"')).includes(`id="${id}"`), `${id} 归到批量按钮组`);
}
// 1.25.20：「AI 建议标签」被请出了这一组。.bulk-actions 整组是 display:none、
// 勾了题才显示；留在里面的话，「让它常驻」写在标记里也不作数 —— 不勾题时
// 页面上仍然一个入口都没有，那正是老师找不到它的原因。
assert.ok(bulk.includes('id="generateSelectedTags"'), "批量条里还得有「AI 建议标签」");
assert.doesNotMatch(bulk.slice(bulk.indexOf('class="bulk-actions"')), /generateSelectedTags/,
  "它不能待在「勾了题才出现」的那一组里");
assert.match(libraryCss, /\.library-suggest-slot \{[^}]*display: flex/, "它自己占的那一格任何时候都显示");
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
assert.match(libraryCss, /\.library-workspace \{[^}]*grid-template-columns: 212px minmax\(0, 1fr\)/, "题目列表拿走侧栏之外的空间");
assert.match(libraryCss, /\.library-filterbar \{[^}]*display: flex[^}]*flex-wrap: wrap/, "筛选和排序在题目列表上方并可换行");
assert.match(libraryCss, /\.library-advanced\[open\] > \.library-filters \{ position: absolute;[^}]*box-shadow:/, "更多筛选以浮层打开，不把题目推下去");
assert.match(libraryCss, /\.rail-collapse \{[^}]*position: sticky/, "收侧栏的小按钮钉在题面上，滚到哪儿都在");
assert.ok(!libraryJs.includes("QBSiteDrawer?.slot?.(\"library\")"), "抽屉里不再放一份专注浏览，只有侧栏接缝上那一个按钮");
assert.ok(libraryHtml.indexOf('id="allQuestionsButton"') < libraryHtml.indexOf('id="sourceSelect"'),
  "试题篮的返回条留在顶栏，且排在筛选工具栏之前");
assert.match(libraryHtml, /id="allQuestionsButton"[^>]*\bhidden\b/, "返回条默认隐藏，切到篮内视图才出现");
const rail = libraryHtml.slice(libraryHtml.indexOf('id="libraryRail"'), libraryHtml.indexOf('id="libraryRailToggle"'));
assert.doesNotMatch(rail, /sourceSelect|typeFilters|sortSelect|advancedFilters/, "来源、题型、排序和更多筛选已从侧栏移出");
// 1.25.17：题型和排序搬进了「更多筛选」。原来那条断言查的是四个控件的先后顺序，
// 顺序一变就红，可它真正要说的是「这四个都在题目上方的筛选条里、不在侧栏」——
// 搬进折叠板之后这句话照样成立。改成查归属，不查先后。
const filterBar = libraryHtml.slice(libraryHtml.indexOf('id="libraryFilterBar"'), libraryHtml.indexOf('id="libraryList"'));
for (const id of ["sourceSelect", "typeFilters", "sortSelect", "advancedFilters"]) {
  assert.ok(filterBar.includes(`id="${id}"`), `${id} 还在题目上方的筛选条里`);
}
assert.match(libraryHtml, /id="typeFacetLabel"[^>]*>题型/, "题型有可见的维度标签，不再和「来源」的「全部」并排混淆");
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
assert.match(libraryCss, /\.library-focus-mode \.library-workspace \{ grid-template-columns: minmax\(0, 1fr\) 310px/,
  "专注浏览收起筛选栏后仍保留右侧题目详情");
assert.match(libraryCss, /\.library-focus-mode \.library-workspace > \.question-inspector \{ grid-column: 2; \}/,
  "专注浏览的题目详情栏位于题卡右侧");
assert.ok(!libraryJs.includes("syncToolbar") && !libraryJs.includes("--library-toolbar-h"),
  "顶部横条没了，吸顶高度那一套要删干净");
assert.ok(!libraryCss.includes(".library-toolbar"), "library-toolbar 已整体改成 library-rail");

// ---- 抽屉的 Esc 不能抢原生对话框的 Esc
const drawerJs = fs.readFileSync(require.resolve("./site-drawer.js"), "utf8");
assert.match(drawerJs, /event\.key === "Escape" && ui\.doc\.querySelector\?\.\("dialog\[open\]"\)/);

// ---- 折叠卡片上直接显示短选项（1.25.26）
// 选择题的题干常常只有一句「下列正确的是」，认得出是哪道题全靠选项。以前卡片上把选项
// 整组藏掉、只留一句「含选项 · 预览」，挑题时得逐张点开。现在短的直接排出来，长的和
// 带图的仍然留给「展开题面」——不然一张卡能占掉半屏，反而更慢。
const readingCss = fs.readFileSync(require.resolve("./library-reading.css"), "utf8");
const optionsSrc = libraryJs.slice(libraryJs.indexOf("  const OPTION_PREVIEW_LIMIT"),
  libraryJs.indexOf("  function card(item)"));
assert.notEqual(optionsSrc.indexOf("function previewOptions"), -1,
  "previewOptions was renamed or moved; update this slice");
const previewOptions = new Function(`${optionsSrc}\nreturn previewOptions;`)();
const shortFour = { A: "2", B: "3", C: "4", D: "8" };
assert.equal(previewOptions({ options: shortFour }), shortFour, "四个短选项整组显示");
assert.equal(Object.keys(previewOptions({ options: {} })).length, 0, "没有选项就什么都不显示");
const shortMath = { A: "0", B: "$\\varnothing\\subseteq\\{0\\}$" };
assert.equal(previewOptions({ options: shortMath }), shortMath, "短公式照样显示：$ 和反斜杠不占纸面宽度");
const longText = "甲、乙两地相距 120 千米，一辆汽车从甲地开往乙地，速度是每小时 60 千米，问需要多少小时";
assert.equal(Object.keys(previewOptions({ options: { A: longText, B: "2" } })).length, 0,
  "长选项整组折叠，不能把一屏塞成一张卡");
assert.equal(Object.keys(previewOptions({ options: { A: "", B: "2" }, figures: [{ slot: "A" }] })).length, 0,
  "选项里带图的也折叠：卡片这一份只画题干配图");
// 卡片得真的用上它，而且选项显示出来以后不再重复写「含选项」。
assert.match(libraryJs, /const shownOptions = expanded \? \{\} : previewOptions\(item\.content\)/,
  "折叠状态用预览选项，展开状态用完整题面");
assert.match(libraryJs, /options: shownOptions/);
assert.match(libraryJs, /!Object\.keys\(shownOptions\)\.length\) notes\.push\("含选项"\)/,
  "选项已经看得见时，就别再挂一句「含选项」");
assert.match(readingCss, /\.library-card\.compact \.qb-options \{[^}]*font-size: 15px/,
  "卡片上的选项收小一号，把「一屏能看几道题」还回去");

// 1.25.33：滚动时不再挂「筛选」入口。筛选就放在最上面，回去靠右下角那颗按钮。
assert.ok(!libraryHtml.includes('id="compactFilters"'), "批量条里不再有「筛选」两个字");
assert.ok(!libraryJs.includes("syncCompactFilters"), "收起/展开那套逻辑整段撤掉");
assert.ok(!libraryCss.includes(".library-compact-filters"), "对应的样式也删干净");
assert.match(libraryHtml, /id="backToTop"/, "右下角留着「回到顶部」");
assert.match(libraryJs, /window\.scrollTo\(\{ top: 0, behavior: "smooth" \}\)/, "点它一下回到最上面，再筛选");

// ── 1.25.31：题多起来以后的手感 ──
// ① 后台刷新只读有任务的那几行。以前每 4 秒把屏上几百道题整屏重读一遍。
assert.match(libraryJs, /async function refreshPendingJobs\(/, "后台刷新走单独的轻量路径");
assert.match(libraryJs, /\/api\/library\?ids=\$\{encodeURIComponent\(ids\)\}&limit=100/,
  "按编号只取有任务的那几道");
assert.doesNotMatch(libraryJs, /setTimeout\(\(\) => load\(\{ quiet: true \}\)/,
  "任务刷新不再触发整屏重读");
assert.match(libraryJs, /if \(!pendingJobItems\(\)\.length\) \{ load\(\{ quiet: true \}\); return; \}/,
  "最后一道做完时仍要完整刷新一次，把「还有活没干」的数字对上");

// ② 滚到底自动接着加载。
assert.match(libraryJs, /new IntersectionObserver\([\s\S]{0,220}load\(\{ append: true \}\)/,
  "滚到底自动加载；按钮留着兜底");
assert.match(libraryJs, /rootMargin: "600px 0px"/, "提前一屏开始取，不用等真的看到按钮");

// ③ 选中当前筛选的全部。
assert.match(libraryHtml, /id="selectAllFiltered"/, "批量条上有「选中全部 N 道」");
assert.match(libraryJs, /query\.set\("ids_only", "1"\)/, "只跟后端要编号，不拉全量内容");
assert.match(libraryJs, /ids\.slice\(0, 500\)\.forEach/, "和试题篮一样，一次最多 500 道");

// ④ 回到顶部。
assert.match(libraryHtml, /id="backToTop"/, "滚深了以后有回顶部的按钮");
assert.match(libraryJs, /scrollTo\(\{ top: 0, behavior: "smooth" \}\)/, "点了平滑滚回去");
assert.match(libraryJs, /top_button\.hidden = window\.scrollY < 900/, "滚过几屏才露出来");

// ⑤ 排序多一个「按题号」。
assert.match(libraryHtml, /<option value="number">按题号<\/option>/);
assert.match(libraryJs, /\["source", "number"\]\.includes\(params\.get\("sort"\)\)/, "URL 里的按题号也认");

// ⑥ 列表列宽有上限：右侧详情收起或窗口很宽时，卡片不再被拉到窗口边缘，
//    题干也不会缩在左边、右边空一大片。
assert.match(libraryCss, /\.library-workspace > \.library-results \{ width: min\(100%, 1040px\); justify-self: center; \}/,
  "题目列最宽 1040，居中");
assert.match(readingCss, /\.library-page \.library-card \.qb-stem-body \{ max-width: 100%; \}/,
  "题干占满卡片，不再卡在 76 个字宽");

console.log("Library layout: results-head folded in, basket and status ride the bulk row, nav tucked into the drawer: OK");
