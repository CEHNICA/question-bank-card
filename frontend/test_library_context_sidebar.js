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
assert.match(js, /function renderExtraTools\(\)[\s\S]*?快速查看[\s\S]*?const batch = node\("details", "library-batch-ai"\)[\s\S]*?AI 批量处理[\s\S]*?batchBox\.append\(batch\)/, "quick browsing and API-consuming batch tasks have separate destinations");
assert.match(js, /batch\.open = aiBatchToolsOpen/, "batch tasks are folded by default and restore their in-page state");
assert.match(js, /batch\.addEventListener\("toggle", \(\) => \{ aiBatchToolsOpen = batch\.open; \}\)/, "the batch disclosure stays in its chosen state during in-page filter refreshes");
assert.doesNotMatch(js.match(/function renderExtraTools\(\)[\s\S]*?\n  function currentFilterPayload/)?.[0] || "", /标签管理/, "the tag manager is not duplicated inside task actions");
assert.doesNotMatch(js, /\$\{message\}。请到“设置 → API 配置”检查服务商、密钥和模型/,
  "unknown worker failures do not falsely blame API configuration");

console.log("Library contextual question sidebar and tag management entry: OK");
