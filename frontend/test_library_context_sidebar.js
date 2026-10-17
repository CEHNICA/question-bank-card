"use strict";

// Context selection stays separate from the checkbox selection used by batch actions.
const assert = require("node:assert/strict");
const fs = require("node:fs");

const html = fs.readFileSync(require.resolve("./library.html"), "utf8");
const js = fs.readFileSync(require.resolve("./library.js"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");
const readingCss = fs.readFileSync(require.resolve("./library-reading.css"), "utf8");

assert.match(html, /id="questionInspector"/, "the library has a dedicated question details panel");
assert.match(html, /<button[^>]*id="libraryTagManager"[^>]*>[\s\S]*?<span>标签管理<\/span>[\s\S]*?<\/button>/, "tag management is directly accessible from the library header");
assert.match(html, /<div id="libraryFilterBar" class="library-filterbar"[\s\S]*?id="sourceSelect"[\s\S]*?id="typeFilters"[\s\S]*?id="sortSelect"[\s\S]*?<details id="advancedFilters"[\s\S]*?<\/details>/,
  "source, type, sort, and advanced filters share the toolbar above the questions");
const rail = html.slice(html.indexOf('id="libraryRail"'), html.indexOf('id="libraryRailToggle"'));
assert.doesNotMatch(rail, /sourceSelect|typeFilters|sortSelect|advancedFilters/, "filter and sort controls no longer occupy the side rail");
assert.match(rail, /hidden inert/, "the former rail is inert and cannot occupy space or receive focus");
const header = html.slice(html.indexOf('<header class="topbar'), html.indexOf('</header>'));
assert.match(header, /library-top-navigation[\s\S]*?id="searchInput"[\s\S]*?id="allQuestionsButton"[\s\S]*?id="basketViewButton"[\s\S]*?id="libraryTagManager"/, "the real search, scope and tag-manager controls share the top navigation");
assert.doesNotMatch(header.slice(header.indexOf('library-top-navigation')), /data-drawer="library"/, "frequent library controls are not silently moved into the global drawer");
assert.match(html, /<div class="library-bulk"[\s\S]*?<div id="aiBatchTools" class="library-batch-toolbar"/, "AI batch operations use the empty question-list toolbar area");
assert.match(html, /class="tag-saved-filter-actions"[\s\S]*?id="saveTagFilter"[\s\S]*?id="deleteTagFilter"/, "saved-filter actions are grouped together below their selector");
assert.match(html, /id="inspectorToggle"[^>]*aria-controls="questionInspector"/, "narrow screens have a visible details-panel control");
assert.match(js, /function selectInspectorQuestion\(item, article = null\)/, "clicking a question updates the contextual panel");
assert.match(js, /event\.target\.closest\("button, a, input, select, textarea, summary, details, label/, "card actions and batch checkboxes do not hijack the context click");
assert.match(js, /function renderInspector\(item\)[\s\S]*?extrasNode\(item\)/, "tags and pending AI suggestions follow the selected question");
assert.match(js, /renderInspector\(state\.catalog\.get\(state\.inspectorId\)/, "background refreshes keep the panel in sync with the selected question");
assert.match(readingCss, /\.library-page \.library-workspace \{[^}]*grid-template-columns: minmax\(0, 1fr\) 288px;/, "desktop dedicates space only to the results and right inspector");
assert.match(css, /body\.library-inspector-open \.question-inspector \{ transform: none; \}/, "the details panel opens as a drawer on narrow screens");
assert.match(js, /setInspectorOpen\(false\);[\s\S]*?state\.basketVisible = next;/, "the basket and question inspector do not cover each other on narrow screens");
assert.match(js, /if \(!window\.matchMedia\("\(max-width: 979px\)"\)\.matches\) setInspectorOpen\(true\)/,
  "a plain card click on narrow screens selects the question without opening a covering drawer");
assert.match(js, /library-card-details", "标签与详情"[\s\S]*?setInspectorOpen\(true\)/,
  "mobile users can deliberately open tags and details from the current card");
assert.match(js, /article\.tabIndex = 0[\s\S]*?article\.addEventListener\("keydown"[\s\S]*?event\.isComposing[\s\S]*?event\.key === "Enter"/,
  "keyboard users can select and open a card without triggering IME composition");
assert.match(js, /node\("details", "question-inspector-info"\)/,
  "secondary metadata is folded so tags remain prominent");
assert.match(js, /inspectorScrolls\.set\(previous, ui\.inspectorContent\.scrollTop\)/,
  "switching context records the previous question's sidebar position");
assert.match(js, /content\.scrollTop = inspectorScrolls\.get\(item\.id\)/,
  "returning to a question restores its sidebar position");
assert.match(js, /question-inspector-target[\s\S]*?右侧信息对应绿色高亮题卡[\s\S]*?定位题卡/,
  "the inspector explains which question it belongs to and can locate that card");
assert.match(js, /function captureLibraryViewport\(\)[\s\S]*?anchorTop[\s\S]*?function restoreLibraryViewport\(position\)/,
  "library refreshes retain the selected question's viewport position");
assert.match(js, /else restoreLibraryViewport\(viewport\)/,
  "quiet reloads restore the reading position unless an explicit focus target takes precedence");
assert.match(css, /\.library-card\.inspector-active[^\{]*\{[^}]*outline:[^}]*box-shadow:/,
  "the question linked to the inspector has a persistent green outline and glow");
assert.match(js, /tag-suggestion-retry-inline[\s\S]*?重新建议[\s\S]*?queueJobs\("tags", \{ ids: \[item\.id\], retry: true \}\)/,
  "a failed tag suggestion can be retried from the same visible error panel");
// 待办入口（待核对、未标知识点）必须常驻在筛选栏上，不能藏进「更多筛选」。
// 它以前就住在那个浮层里，而浮层在三组筛选都为空时会把整个折叠板一起隐藏 ——
// 于是「有 87 题待核对」这种提示跟着从页面上消失，用户完全不知道还有活要干。
// 下面几条各自钉住一个环节：落在哪、隐藏判断算不算它、有没有格子、该消失时是不是真消失。
const advancedBlock = html.match(/<details id="advancedFilters"[\s\S]*?<\/details>/)?.[0] || "";
assert.doesNotMatch(advancedBlock, /id="tagQuickTools"/,
  "the pending-work entry is not parked inside the More Filters disclosure");
assert.ok(html.indexOf('id="tagQuickTools"')
    > html.indexOf("</details>", html.indexOf('<details id="advancedFilters"')),
  "the pending-work entry sits in the filter bar, after More Filters");
assert.doesNotMatch(js, /\$\("advancedFilters"\)\.hidden = \[[^\]]*extraTools/,
  "hiding More Filters never depends on the pending-work entry");
assert.match(readingCss, /grid-template-areas:[^;]*"[^"]*quick[^"]*"/,
  "the filter bar grid reserves a slot for the pending-work entry");
// 上一条只查得到「排版里提到了 quick」，元素本身没被放进格子它照样会过。
// 少了这一行，待办入口会被自动排到第二行跟题型抢格子，所以两条要钉在一起。
assert.match(readingCss, /\.library-extra-tools\s*\{\s*grid-area:\s*quick/,
  "the pending-work entry is actually placed in its reserved slot");
assert.match(css.concat(readingCss), /\.library-extra-tools\[hidden\][^{]*\{[^}]*display:\s*none/,
  "the pending-work entry really disappears when nothing is pending");
assert.match(js, /function renderExtraTools\(\)[\s\S]*?box\.append\(\.\.\.quickActions\)/,
  "pending-work buttons are rendered into the always-visible container");
// 「AI 批量处理」这个入口以前只有四个字，看不出要花钱、也不知道会动多少题，
// 又贴在右边缘像个小灰按钮，于是几乎没人点。名字、代价提示、以及有活干时
// 直接显示「还剩多少道」，三样都要钉住，缺一样又会退回成一个藏起来的入口。
// 名字本身还必须说清「处理什么」：改成「AI 批量处理题目」之后仍然有人问
// 「批量处理啥呀」——真正被补的就是标签和答案，必须出现在名字里。
assert.match(js, /function renderExtraTools\(\)[\s\S]*?const batch = node\("details", "library-batch-ai"\)[\s\S]*?批量补标签和答案[\s\S]*?batchBox\.append\(batch\)/,
  "quick browsing and API-consuming batch tasks have separate destinations");
assert.match(js, /node\("summary", "", "批量补标签和答案"\)/,
  "the batch entry names the two things it actually fills in");
assert.match(js, /node\("span", "library-batch-cost", "要花额度"\)/,
  "the batch entry says on its face that it costs quota");
assert.match(js, /node\("span", "library-batch-live", `处理中 \$\{working\} 道`\)/,
  "while AI is working, the entry itself says how many questions are left");
assert.match(css.concat(readingCss), /\.library-batch-toolbar \.library-batch-ai > summary\s*\{[^}]*background:\s*var\(--accent\)/,
  "the batch entry is a solid accent button, not a quiet grey one that gets missed");
// 题型筛选项以前会换行：窗口稍窄一点，最后一个「解答题」就被挤到第二行，
// 一眼看去像两排选项；左边「题型」两个字还被挤成一字一行。锁成一行、六个平分宽度。
// 选择器必须对得上真实结构：题型那组是 .library-facet 的孙节点。写成
// 「.library-filterbar > #typeFilters」的话规则从头到尾没生效过，而上面两条
// 断言照样是绿的 —— 排版坏了，没有任何一条会响。
assert.match(html, /<div class="library-facet">[\s\S]*?id="typeFilters"/,
  "the type filter really is a grandchild of the filter bar, not a direct child");
assert.match(readingCss, /\.library-filterbar > \.library-facet \{ grid-area: types/,
  "the type facet is placed in the reserved full-width row instead of being auto-placed");
assert.match(readingCss, /\.library-facet > #typeFilters\s*\{[^}]*flex-wrap:\s*nowrap/,
  "the type filter never wraps to a second row");
assert.match(readingCss, /\.library-facet > #typeFilters \.draft-filter\s*\{[^}]*flex:\s*1 1 0/,
  "the type filter buttons share one row evenly instead of wrapping");
assert.doesNotMatch(css.concat(readingCss), /\.library-filterbar > #typeFilters/,
  "no rule still targets the type filter as a direct child of the filter bar");
assert.match(js, /batch\.open = aiBatchToolsOpen/, "batch tasks are folded by default and restore their in-page state");
assert.match(js, /batch\.addEventListener\("toggle", \(\) => \{ aiBatchToolsOpen = batch\.open; \}\)/, "the batch disclosure stays in its chosen state during in-page filter refreshes");
assert.doesNotMatch(js.match(/function renderExtraTools\(\)[\s\S]*?\n  function currentFilterPayload/)?.[0] || "", /标签管理/, "the tag manager is not duplicated inside task actions");
assert.doesNotMatch(js, /\$\{message\}。请到“设置 → API 配置”检查服务商、密钥和模型/,
  "unknown worker failures do not falsely blame API configuration");

console.log("Library contextual question sidebar and tag management entry: OK");
