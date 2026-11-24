"use strict";

// 改字 shows what will be saved while the user types.  1.12.7 turned it into a
// fullscreen workspace: 左上原卷 / 左下编辑 / 右边整列预览，顶栏一条操作栏。
// The preview no longer chases the stem box around — the three zones are a grid,
// so their top edges line up on their own and no pixel nudging is needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const helpers = fs.readFileSync(path.join(__dirname, "qb-editor-helpers.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const editorCss = fs.readFileSync(path.join(__dirname, "question-editor.css"), "utf8");

// Every keystroke re-renders on the next frame instead of after a pause.
assert.match(js, /frame = requestAnimationFrame\(\(\) => \{\s*frame = 0;\s*const data = collect\(\);\s*const scroll = [^;]+;\s*R\.renderQuestion\(\s*preview,/);
assert.doesNotMatch(js, /timer = setTimeout\(\(\) => \{\s*const data = collect\(\)/);
// The three zones are laid out by one grid on the card, so the editor form and the
// preview have to be direct children of it rather than of the card body.
assert.match(js, /card\.append\(bar, previewBox\);\s*card\.querySelector\("\.card-body"\)\.append\(editor\);/);
assert.match(js, /const editor = el\("form", "editor"\);/);
// 1.26.30：编辑面板里那个题源输入框撤了 —— 改题源只在「更多」里做。
assert.match(js, /editor\.append\(stemRow, tableTools, optionBox, extra\);/);
// The pixel-nudging layout code is gone with it: a margin that moves the thing it
// measures, guarded by window width and image load timing, was the actual bug.
assert.doesNotMatch(js, /const alignPreviewWithStem|const placePreview|--preview-room/);
assert.doesNotMatch(css, /\.editor-preview-box\.beside|editor-head|editor-actions \{/);
// The whole review page gives way to the editor: no paper list, no app top bar,
// no page scroll behind a fixed card.
assert.match(js, /document\.body\.classList\.add\("qb-editing"\);/);
assert.match(js, /document\.body\.classList\.remove\("qb-editing"\);/);
assert.match(css, /body\.qb-editing \.topbar, body\.qb-editing \.sidebar, body\.qb-editing \.to-top \{ display: none; \}/);
assert.match(css, /body\.qb-editing \{ overflow: hidden; \}/);
assert.match(css, /\.card\.editing \{\s*position: fixed; inset: 0; z-index: 60; height: 100dvh;/);
assert.match(css, /\.card\.editing \{[^}]*grid-template-areas: "bar bar" "source preview" "body preview";/);
assert.match(css, /\.card\.editing > \.card-source \{ grid-area: source;/);
assert.match(css, /\.card\.editing > \.card-body \{ grid-area: body;[^}]*overflow-y: auto;/);
assert.match(css, /\.card\.editing > \.editor-preview-box \{ grid-area: preview;/);
assert.match(css, /^\.editor-bar \{ grid-area: bar;/m);
assert.match(editorCss, /\.editor-bar \{\s*grid-area: bar;\s*display: grid;\s*grid-template-columns: minmax\(0, 1fr\) auto minmax\(0, 1fr\);/);
// A tall original is capped and scrolls inside its own zone instead of pushing the
// editing area off the screen.
assert.match(css, /\.card\.editing \.source-sticky \{[^}]*max-height: 42dvh;/);
assert.match(css, /\.card\.editing \.source-sticky > \.crop \{[^}]*overflow-y: auto;/);
// Nothing may still pin a bar to the bottom, or reserve space for it down there.
assert.doesNotMatch(css, /\.card\.editing [^{]*\{[^}]*position: sticky; bottom/);
assert.doesNotMatch(css, /padding-bottom: 62px/);
// 精简三分栏顶栏由 QBEditorHelpers.buildEditorBar 统一渲染，携带返回、题号分段器、题型和保存。
assert.match(js, /H\.buildEditorBar\(\{[\s\S]*?onBack:\s*\(\)\s*=>\s*discardEdits\(\[q\.id\]\)/);
assert.match(js, /const jump = async \(delta\) => \{[\s\S]*?if \(!\(await discardEdits\(\[q\.id\]\)\)\) return;/);
// Switching questions only walks cards that can actually be opened.
assert.match(js, /const editableSiblings = \(\) => \[\.\.\.document\.querySelectorAll\("\.card:not\(\.compact\)"\)\]/);
assert.match(js, /const jump = async \(delta\) => \{\s*const list = editableSiblings\(\);\s*const from = list\.findIndex\(\(item\) => item\.id === q\.id\);/);
assert.doesNotMatch(js, /const siblings = state\.questions\.filter\(visible\);/);
assert.match(js, /countText: `第 \$\{q\.number\} 题 \/ 共 \$\{state\.questions\.length\} 题`/);
// The stem box grows with its text via shared fitAutoHeight helper.
assert.match(js, /H\.fitAutoHeight\(stem, \{ minRows: 2, maxHeightPx: Math\.round\(window\.innerHeight \* 0\.4\) \}\)/);
assert.match(helpers, /measured > 0\) textarea\.style\.height = `\$\{Math\.min\(measured \+ 2, maxHeightPx\)\}px`;/);
// The bar is a child of the card, not of the <form>, so save button uses type="button" + requestSubmit().
assert.match(helpers, /const saveButton = createEl\("button", "button primary editor-bar-save", opts\.saveLabel \|\| "保存"\);\s*saveButton\.type = "button";/);
assert.match(js, /onSave:\s*\(\)\s*=>\s*editor\.requestSubmit\(\)/);
// 统一工具条与默认展开的答案区（含填空横线按钮、A-H 动态选项、AI 推荐答案）
assert.match(js, /H\.buildStemToolbar\(/);
assert.match(js, /H\.buildDynamicOptionEditor\(/);
assert.match(js, /H\.createAiRecommendWidget\(/);
assert.match(js, /extra\.open = true;/);
// Closing the editor takes the whole workspace with it, body class included.
assert.match(js, /window\.removeEventListener\("resize", handleResize\);\s*previewBox\.remove\(\);\s*bar\.remove\(\);\s*editor\.remove\(\);\s*document\.body\.classList\.remove\("qb-editing"\);/);

console.log("edit preview checks: OK");
