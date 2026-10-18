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
// 1.25.17：题型和排序搬进了「更多筛选」。这条断言原来查的是四个控件的先后顺序，
// 而它真正要说的是「这四个都在题目上方的筛选条里」—— 搬进折叠板之后照样成立。
// 改成查归属，不查先后。
const filterBar = html.slice(html.indexOf('id="libraryFilterBar"'), html.indexOf('id="libraryList"'));
for (const id of ["sourceSelect", "typeFilters", "sortSelect", "advancedFilters"]) {
  assert.ok(filterBar.includes(`id="${id}"`), `${id} is still in the toolbar above the questions`);
}
const rail = html.slice(html.indexOf('id="libraryRail"'), html.indexOf('id="libraryRailToggle"'));
assert.doesNotMatch(rail, /sourceSelect|typeFilters|sortSelect|advancedFilters/, "filter and sort controls no longer occupy the side rail");
assert.match(rail, /hidden inert/, "the former rail is inert and cannot occupy space or receive focus");
const header = html.slice(html.indexOf('<header class="topbar'), html.indexOf('</header>'));
assert.match(header, /library-top-navigation[\s\S]*?id="searchInput"[\s\S]*?id="allQuestionsButton"[\s\S]*?id="basketViewButton"[\s\S]*?id="libraryTagManager"/, "the real search, scope and tag-manager controls share the top navigation");
assert.doesNotMatch(header.slice(header.indexOf('library-top-navigation')), /data-drawer="library"/, "frequent library controls are not silently moved into the global drawer");
assert.match(html, /<div class="library-bulk"[\s\S]*?<div id="libraryTodos" class="library-todos"/, "AI batch operations use the empty question-list toolbar area");
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
assert.match(toggleNode.title, /标签和待核对建议/, "the hover line says what lives in that panel");
sandbox.markInspectorToggle(false);
assert.equal(toggleNode.getAttribute("aria-expanded"), "false");
assert.equal(toggleNode.getAttribute("aria-label"), "展开右侧详情", "a collapsed panel offers to bring it back");
assert.match(toggleNode.title, /标签和待核对建议/);
sandbox.markInspectorToggle(true, { disabled: true });
assert.equal(toggleNode.getAttribute("aria-expanded"), "false", "a disabled toggle is not reported as open");
assert.match(toggleNode.title, /先在左边点一道题/, "a greyed-out control says why it is grey, not just that it is grey");
vm.runInNewContext(toggleCode, { ui: { inspectorToggle: null } });
// 1.25.19：「查看当前题面」删了，面板里只剩「题目信息」这一条折叠行，
// 单独一条反而要保证手指头点得着。
assert.match(readingCss, /\.library-page \.question-inspector-info > summary \{[^}]*min-height: 30px;/,
  "the one remaining panel disclosure row is tall enough to click");
assert.doesNotMatch(readingCss, /question-inspector-preview/, "the removed 题面 preview has no styles left behind");
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
// 1.25.19：面板不再重复题卡上已有的东西，也就没有「当前选中 / 定位题卡」那一行。
// 判断面板讲的是哪道题，改由题卡上那道常驻的绿框负责（下面那条断言盯着它）。
assert.doesNotMatch(js, /question-inspector-target/, "the panel no longer repeats the card's own identity");
assert.doesNotMatch(js, /question-inspector-source/, "the paper name lives on the card, not twice more in the panel");
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
// 1.25.20：待办从筛选行搬到了批量行，量的还是原来那件事 —— 它绝不能住进
// 「更多筛选」那个折叠板。那个折叠板在三组筛选都为空时会整个藏起来，
// 待办跟着进去的话，「有 439 题建议待采纳」就会一起从页面上消失。
const advancedBlock = html.match(/<details id="advancedFilters"[\s\S]*?<\/details>/)?.[0] || "";
assert.doesNotMatch(advancedBlock, /id="libraryTodos"/,
  "the pending-work entry is not parked inside the More Filters disclosure");
assert.ok(html.indexOf('id="libraryTodos"')
    > html.indexOf("</details>", html.indexOf('<details id="advancedFilters"')),
  "the pending-work entry sits after More Filters, on the question-list row");
assert.doesNotMatch(js, /\$\("advancedFilters"\)\.hidden = \[[^\]]*ui\.todos/,
  "hiding More Filters never depends on the pending-work entry");
// 1.25.20：筛选行现在只剩「来源」和「更多筛选」。原来那四列里的 sort（1.25.17
// 就空了）和 quick（这轮空了）必须一起从栅格里拿掉 —— 空着的 auto 列会变成两道
// 空隙，把「更多筛选」推离来源，看着像中间漏了一块。
assert.doesNotMatch(readingCss, /grid-template-areas:[^;]*"[^"]*\bquick\b[^"]*"/,
  "the filter bar grid no longer reserves a slot for a pending-work entry that moved away");
assert.doesNotMatch(readingCss, /grid-template-areas:[^;]*"[^"]*\bsort\b[^"]*"/,
  "the empty sort slot is gone too");
assert.match(readingCss, /grid-template-areas:\s*"source advanced"/,
  "the filter bar grid is exactly the two things that are still there");
assert.match(css.concat(readingCss), /\.library-todos:not\(\[hidden\]\)[^{]*\{[^}]*display:\s*flex/,
  "the pending-work entry is shown whenever there is something pending");
assert.match(js, /function renderTodos\(\)[\s\S]*?look\.append\(\.\.\.quickActions\)/,
  "pending-work buttons are rendered into the always-visible container");
// 1.25.20：三样「还有活没干」合成一处折叠条。折叠起来的东西要让人看得见里面有什么，
// 所以名字上直接把那三样数出来 —— 「让 AI 提标签建议」这句话整屏都没出现过，
// 老师因此以为这个功能不存在。
assert.match(js, /function renderTodos\(\)[\s\S]*?const menu = node\("details", "library-todo-menu"\)/,
  "the pending-work filters and the AI jobs share one container");
assert.match(js, /`\$\{pendingCount\} 题建议待采纳`/,
  "the entry counts how many tag suggestions are waiting to be accepted");
assert.match(js, /`\$\{missingCount\} 题没打标签`/,
  "the entry counts how many questions have no knowledge tag yet");
assert.match(js, /`AI 能补 \$\{batchActions\.length\} 项`/,
  "the entry says how many jobs AI can fill in, instead of hiding them behind a ▾");
assert.match(js, /node\("span", "library-todo-lead", working \? "AI 正在处理" : "还有活没干"\)/,
  "the entry says what it is, in the teacher's words");
// 「要花额度」原来占着入口的名字的一半。它是警告不是动作，挤在名字里只会让人
// 认不出这个入口是干什么的 —— 现在挪进展开后的第一行和每颗按钮的悬停说明。
assert.doesNotMatch(js, /function renderTodos\(\)[\s\S]*?node\("span", "library-batch-cost"/,
  "the cost warning no longer takes up room in the entry's name");
assert.match(js, /node\("p", "library-todo-note", apiReady[\s\S]*?消耗额度/,
  "the cost warning still appears inside the panel, just not in the name");
assert.match(js, /node\("span", "library-batch-live", `处理中 \$\{working\} 道`\)/,
  "while AI is working, the entry itself says how many questions are left");
// 这个按钮的显眼程度走了个来回，正好说明单看「显不显眼」判断不了对错：
// 原来太低调、贴右边没人点；改成实心主色又成了全场视觉焦点 —— 可它恰恰是
// 一屏里最贵、最不该随手点的那个（每题花一次额度）。
// 现在的规矩是：平时和「更多筛选」一样低调，只有 AI 真的在逐题处理时才亮成主色。
// 两条都要钉住，只钉一条的话，另一条一定会被人「顺手改回去」。
assert.doesNotMatch(css, /\.library-todos \.library-todo-summary\s*\{[^}]*background:\s*var\(--accent\)/,
  "the batch entry stays quiet while nothing is running — it is the most expensive button on the page");
assert.match(css, /\.library-todos \.library-todo-menu\[data-working\] > \.library-todo-summary\s*\{[^}]*background:\s*var\(--accent\)/,
  "the batch entry lights up exactly while AI is working through the queue");
assert.match(js, /if \(working\) menu\.dataset\.working = "true"/,
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
// 1.25.20：待办搬去批量行以后，筛选行那套「贴着下划线、和题型一个长相」的规则
// 一起失效了。图标不在这条上管，但下划线长相要留着 —— 它现在是折叠区里的筛选，
// 展开后仍然得一眼看得出「这是筛选，不是做事」。
assert.doesNotMatch(css.concat(readingCss), /\.library-filterbar > \.library-extra-tools/,
  "the old filter-bar pending-work rules can no longer match anything");
assert.doesNotMatch(css.concat(readingCss), /\.library-extra-tools \.button/,
  "the pending-work button styling moved with the control");
assert.doesNotMatch(css.concat(readingCss), /\.library-filterbar > #typeFilters/,
  "no rule still targets the type filter as a direct child of the filter bar");
assert.match(js, /menu\.open = todoMenuOpen/, "batch tasks are folded by default and restore their in-page state");
assert.match(js, /menu\.addEventListener\("toggle", \(\) => \{ todoMenuOpen = menu\.open; \}\)/, "the batch disclosure stays in its chosen state during in-page filter refreshes");
assert.doesNotMatch(js.match(/function renderTodos\(\)[\s\S]*?\n  function currentFilterPayload/)?.[0] || "", /标签管理/, "the tag manager is not duplicated inside task actions");
assert.doesNotMatch(js, /\$\{message\}。请到“设置 → API 配置”检查服务商、密钥和模型/,
  "unknown worker failures do not falsely blame API configuration");

console.log("Library contextual question sidebar and tag management entry: OK");
