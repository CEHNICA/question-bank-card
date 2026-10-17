"use strict";

// Context selection stays separate from the checkbox selection used by batch actions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

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

// 「题目详情」这个按钮是个开关，但字面只有四个字的���称：不写现在开着还是收着，也不说
// 按了会发生什么。同一个页面上另一个收侧栏的开关就写着「收起筛选栏」。这里跑真的那个
// 函数，确认「状态」和「说法」是同一个动作一起变的 —— 只改其中一个，正则查不出来。
const toggleCode = js.slice(js.indexOf("  function markInspectorToggle("), js.indexOf('  function setInspectorOpen('));
assert.notEqual(toggleCode.indexOf("markInspectorToggle"), -1, "markInspectorToggle was renamed or moved; update this slice");
const toggleNode = { attrs: {}, title: "", setAttribute(name, value) { this.attrs[name] = String(value); }, getAttribute(name) { return this.attrs[name]; } };
const sandbox = { ui: { inspectorToggle: toggleNode } };
vm.runInNewContext(toggleCode, sandbox);
sandbox.markInspectorToggle(true);
assert.equal(toggleNode.getAttribute("aria-expanded"), "true");
assert.equal(toggleNode.getAttribute("aria-label"), "收起右侧详情");
assert.match(toggleNode.title, /来源、标签和待核对建议/, "the hover line says what lives in that panel");
sandbox.markInspectorToggle(false);
assert.equal(toggleNode.getAttribute("aria-expanded"), "false");
assert.equal(toggleNode.getAttribute("aria-label"), "展开右侧详情", "a collapsed panel offers to bring it back");
assert.match(toggleNode.title, /来源、标签和待核对建议/);
sandbox.markInspectorToggle(true, { disabled: true });
assert.equal(toggleNode.getAttribute("aria-expanded"), "false", "a disabled toggle is not reported as open");
assert.match(toggleNode.title, /先在左边点一道题/, "a greyed-out control says why it is grey, not just that it is grey");
vm.runInNewContext(toggleCode, { ui: { inspectorToggle: null } });
// 「查看当前题面」和「题目信息」是同一种东西。以前一条 30px、一条 18px，18px 那条点不准。
assert.match(readingCss, /\.question-inspector-preview > summary,\s*\n?\s*\.library-page \.question-inspector-info > summary \{ display: flex; align-items: center; min-height: 30px; \}/,
  "both panel disclosure rows get the same clickable height");
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

// 待核对的 AI 建议必须直接摊在右侧详情面板上，而不是先摆一个「去核对」的按钮。
// 面板那块下面本来就是空着的，而核对是最高频的动作 —— 让人点一下才看得见 AI
// 建议了什么，等于把最该显眼的东西藏进另一个窗口。
assert.match(js, /tagSuggestionNode\(item, \{ embedded: true \}\)/,
  "the inspector lays the pending suggestions out in place instead of behind a button");
assert.match(js, /if \(panel\) box\.append\(panel\);\s*else \{[\s\S]{0,400}?tag-suggestion-open/,
  "the way-into-the-window button is only a fallback for when no suggestion row could be drawn");
// 「核对」是完整一件事：看、改名、采纳/拒绝。以前被拆成两半（面板只给按钮、其余在
// 窗口里），改个名再采纳要在两边来回跳。现在整件事都在面板上做完。
assert.match(js, /function focusInspectorTag\(item\) \{[\s\S]*?renderInspector\(item\)[\s\S]*?tag-suggestion-card/,
  "the tag window can jump straight to that question's suggestion list in the panel");
// renderInspector 见到同一道题会直接跳过，那块建议清单就不会重画，滚也滚不到。
// 所以清空「已渲染」标记必须在重画**之前** —— 顺序反了等于没清。
assert.match(js, /inspectorRenderedKey = "";\s*renderInspector\(item\);/,
  "the jump forces a redraw before scrolling to the list, not after");
// 反过来，标签窗口里不能再摆一份同样的建议：两份内容看两遍、还得对得上两份。
assert.doesNotMatch(js, /tagSuggestionNode\(item, \{ embedded: true \}\); if \(!card\) return;/,
  "the tag window no longer renders a second copy of the suggestion list");
assert.match(js, /"去右边面板核对"/,
  "the tag window points at the panel instead of showing a duplicate list");
assert.match(js, /条待核对 · 在右边详情面板上核对/,
  "the count in the tag window says where to go, rather than implying you can decide here");
assert.doesNotMatch(js, /已经贴上的、AI 建议的、想加的，都在这个窗口里/,
  "the window no longer claims that accepting suggestions happens inside it");
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
// 这个按钮的显眼程度走了个来回，正好说明单看「显不显眼」判断不了对错：
// 原来太低调、贴右边没人点；改成实心主色又成了全场视觉焦点 —— 可它恰恰是
// 一屏里最贵、最不该随手点的那个（每题花一次额度）。
// 现在的规矩是：平时和「更多筛选」一样低调，只有 AI 真的在逐题处理时才亮成主色。
// 两条都要钉住，只钉一条的话，另一条一定会被人「顺手改回去」。
assert.doesNotMatch(css, /\.library-batch-toolbar \.library-batch-ai > summary\s*\{[^}]*background:\s*var\(--accent\)/,
  "the batch entry stays quiet while nothing is running — it is the most expensive button on the page");
assert.match(css, /\.library-batch-toolbar \.library-batch-ai\[data-working\] > summary\s*\{[^}]*background:\s*var\(--accent\)/,
  "the batch entry lights up exactly while AI is working through the queue");
assert.match(js, /if \(working\) batch\.dataset\.working = "true"/,
  "the entry is marked as working from the same number that drives 处理中 N 道");
// 题型筛选项以前会换行：窗口稍窄一点，最后一个「解答题」就被挤到第二行，
// 一眼看去像两排选项；左边「题型」两个字还被挤成一字一行。
// 选择器必须对得上真实结构：题型那组是 .library-facet 的孙节点。写成
// 「.library-filterbar > #typeFilters」的话规则从头到尾没生效过，而上面两条
// 断言照样是绿的 —— 排版坏了，没有任何一条会响。
assert.match(html, /<div class="library-facet">[\s\S]*?id="typeFilters"/,
  "the type filter really is a grandchild of the filter bar, not a direct child");
assert.match(readingCss, /\.library-filterbar > \.library-facet \{ grid-area: types/,
  "the type facet is placed in the reserved full-width row instead of being auto-placed");
assert.match(readingCss, /\.library-facet > #typeFilters\s*\{[^}]*flex-wrap:\s*nowrap/,
  "the type filter never wraps to a second row");
// 但也不能平分整行：平分会让「判断题 0」这种一格里空着一大片，一屏 12 个数字
// 再配上大片留白，扫不出重点，只觉得乱。宽度必须跟着内容走。
assert.match(readingCss, /\.library-facet > #typeFilters \.draft-filter\s*\{[^}]*flex:\s*0 1 auto/,
  "type filter buttons take only the width they need instead of each claiming an equal slice");
// 待办入口和题型是同一种东西（点了就筛一遍），以前却挂放大镜图标、长得和题型
// 不一样，一屏里出现三种筛选长相。图标要去掉，外观要和题型统一。
assert.doesNotMatch(js, /AI 建议待核对 · \$\{pendingCount\} 题[`'",\s][^)]*"search"/,
  "the pending-review filter no longer wears a search icon that makes it look like a lookup");
assert.doesNotMatch(js, /未标知识点 · \$\{missingCount\} 题[`'",\s][^)]*"search"/,
  "the untagged filter no longer wears a search icon either");
assert.match(readingCss, /\.library-extra-tools \.button\s*\{[^}]*border-bottom:\s*2px solid transparent/,
  "pending-work filters now look like the type filters instead of a third style");
assert.doesNotMatch(css.concat(readingCss), /\.library-filterbar > #typeFilters/,
  "no rule still targets the type filter as a direct child of the filter bar");
assert.match(js, /batch\.open = aiBatchToolsOpen/, "batch tasks are folded by default and restore their in-page state");
assert.match(js, /batch\.addEventListener\("toggle", \(\) => \{ aiBatchToolsOpen = batch\.open; \}\)/, "the batch disclosure stays in its chosen state during in-page filter refreshes");
assert.doesNotMatch(js.match(/function renderExtraTools\(\)[\s\S]*?\n  function currentFilterPayload/)?.[0] || "", /标签管理/, "the tag manager is not duplicated inside task actions");
assert.doesNotMatch(js, /\$\{message\}。请到“设置 → API 配置”检查服务商、密钥和模型/,
  "unknown worker failures do not falsely blame API configuration");

console.log("Library contextual question sidebar and tag management entry: OK");
