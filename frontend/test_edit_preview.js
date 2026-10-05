"use strict";

// 改字 shows what will be saved while the user types.  1.12.7 turned it into a
// fullscreen workspace: 左上原卷 / 左下编辑 / 右边整列预览，顶栏一条操作栏。
// The preview no longer chases the stem box around — the three zones are a grid,
// so their top edges line up on their own and no pixel nudging is needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// Every keystroke re-renders on the next frame instead of after a pause.
assert.match(js, /frame = requestAnimationFrame\(\(\) => \{\s*frame = 0;\s*const data = collect\(\);\s*const scroll = [^;]+;\s*R\.renderQuestion\(preview,/);
assert.doesNotMatch(js, /timer = setTimeout\(\(\) => \{\s*const data = collect\(\)/);
// The three zones are laid out by one grid on the card, so the editor form and the
// preview have to be direct children of it rather than of the card body.
assert.match(js, /card\.append\(bar, previewBox\);\s*card\.querySelector\("\.card-body"\)\.append\(editor\);/);
assert.match(js, /const editor = el\("form", "editor"\);/);
assert.match(js, /editor\.append\(originRow, stemRow, tableTools, optionBox, extra\);/);
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
// A tall original is capped and scrolls inside its own zone instead of pushing the
// editing area off the screen.
assert.match(css, /\.card\.editing \.source-sticky \{[^}]*max-height: 42dvh;/);
assert.match(css, /\.card\.editing \.source-sticky > \.crop \{[^}]*overflow-y: auto;/);
// Nothing may still pin a bar to the bottom, or reserve space for it down there.
assert.doesNotMatch(css, /\.card\.editing [^{]*\{[^}]*position: sticky; bottom/);
assert.doesNotMatch(css, /padding-bottom: 62px/);
// The bar carries 返回, 上一题/下一题, 题型 and 保存 — replacing the old 取消 button,
// which did exactly what 返回 does.
assert.match(js, /const back = button\("← 返回", "", \(\) => discardEdits\(\[q\.id\]\)\);/);
assert.match(js, /const jump = async \(delta\) => \{[\s\S]*?if \(!\(await discardEdits\(\[q\.id\]\)\)\) return;/);
// Switching questions only walks cards that can actually be opened. An approved but
// unexpanded question renders as a one-line summary with no body, so jumping there
// used to leave the editor closed with nothing open — 1.12.7 hit it on the walk.
// The list is recomputed on every click: grabbing it once when the bar is built meant
// the closed editor kept a stale index and a stale node, so "next" did nothing and
// "previous" walked forward.
assert.match(js, /const editableSiblings = \(\) => \[\.\.\.document\.querySelectorAll\("\.card:not\(\.compact\)"\)\]/);
assert.match(js, /const jump = async \(delta\) => \{\s*const list = editableSiblings\(\);\s*const from = list\.findIndex\(\(item\) => item\.id === q\.id\);/);
assert.doesNotMatch(js, /const siblings = state\.questions\.filter\(visible\);/);
assert.match(js, /place\.append\(el\("span", "editor-bar-count", `第 \$\{q\.number\} 题 \/ 共 \$\{state\.questions\.length\} 题`\)\);/);
// The stem box grows with its text.
assert.match(js, /stem\.style\.height = `\$\{Math\.min\(stem\.scrollHeight \+ 2, Math\.round\(window\.innerHeight \* 0\.4\)\)\}px`;/);
// The bar is a child of the card, not of the <form>, so a type=submit button in it
// silently does nothing — the form never submits and 保存 looks broken. 1.12.7 hit it.
assert.match(js, /const saveButton = el\("button", "button primary", "保存"\);\s*saveButton\.type = "button";\s*saveButton\.addEventListener\("click", \(\) => editor\.requestSubmit\(\)\);/);
assert.doesNotMatch(js, /const saveButton = el\("button", "button primary", "保存"\);\s*saveButton\.type = "submit";/);
// Closing the editor takes the whole workspace with it, body class included.
assert.match(js, /window\.removeEventListener\("resize", fitStem\);\s*previewBox\.remove\(\);\s*bar\.remove\(\);\s*editor\.remove\(\);\s*document\.body\.classList\.remove\("qb-editing"\);/);

console.log("edit preview checks: OK");
