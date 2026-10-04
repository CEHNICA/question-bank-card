"use strict";

// 改字 shows what will be saved while the user types: the preview is typeset
// every frame, sits next to the original (or right under the stem box), and
// leaves with the editor.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// Every keystroke re-renders on the next frame instead of after a pause.
assert.match(js, /frame = requestAnimationFrame\(\(\) => \{\s*frame = 0;\s*const data = collect\(\);\s*const scroll = [^;]+;\s*R\.renderQuestion\(preview,/);
assert.doesNotMatch(js, /timer = setTimeout\(\(\) => \{\s*const data = collect\(\)/);
// 1.12.6: the preview sits in the left column, under the original page, with its
// top edge aligned to the stem box on the right.  Three layouts were tried and
// only this one survived the user's eyes:
//   · preview in the left column, 70px below the input  <- the original bug
//   · stem and preview side by side inside the editor column: that column is only
//     551px, so each half was 265px (too narrow to type a long stem into), and the
//     two column labels were different heights so the boxes still did not line up
//   · preview under the stem, full width: aligned, but the user wants it on the left
assert.match(js, /editor\.append\(head, typeRow, originRow, stemRow, tableTools, optionBox, extra\);/);
assert.doesNotMatch(js, /editor-stem-pair|stemPair/);
assert.match(js, /if \(previewBox\.parentElement !== source\) source\.append\(previewBox\);/);
// Narrow screens have no sticky left column, so the preview falls back below the stem.
assert.match(js, /\} else if \(previewBox\.parentElement !== editor \|\| previewBox\.previousElementSibling !== stemRow\) \{\s*stemRow\.after\(previewBox\);/);
assert.match(js, /previewBox\.classList\.toggle\("beside", beside\);/);
// Alignment is measured, not guessed: moving the box also moves itself, and
// fitStem/showPosition can still change how many lines the stem takes afterwards.
assert.match(js, /const alignPreviewWithStem = \(\) => \{[\s\S]*?for \(let pass = 0; pass < 3; pass \+= 1\) \{[\s\S]*?previewBox\.style\.marginTop = /);
assert.match(js, /if \(beside\) alignPreviewWithStem\(\);/);
// The save bar moved to the top of the panel, next to the title.
assert.match(js, /const head = el\("div", "editor-head"\);/);
assert.match(js, /head\.append\(title, bar\);/);
assert.match(css, /\.editor-preview-box\.beside \{[^}]*border-top: 1px solid var\(--line\);/);
assert.match(css, /\.editor-preview \{ max-height: var\(--preview-room, none\); overflow: auto;/);
assert.match(css, /\.editor-head \{[^}]*position: sticky; top: calc\(var\(--topbar-h\) \+ 6px\);/);
assert.match(css, /\.source-sticky \{ position: sticky; top: calc\(var\(--topbar-h\) \+ 6px\);/);
// Nothing may still pin the bar to the bottom, or reserve space for it down there.
assert.doesNotMatch(css, /\.card\.editing \.editor-actions \{ position: sticky; bottom/);
assert.doesNotMatch(css, /\.card\.editing \.card-body \{ padding-bottom: 62px; \}/);
// The stem box grows with its text so the preview beside it stays in sight.
assert.match(js, /stem\.style\.height = `\$\{Math\.min\(stem\.scrollHeight \+ 2, Math\.round\(window\.innerHeight \* 0\.4\)\)\}px`;/);
// Closing the editor takes the preview with it.
assert.match(js, /cancelAnimationFrame\(frame\);[\s\S]*?window\.removeEventListener\("resize", relayout\);\s*previewBox\.remove\(\);/);

console.log("edit preview checks: OK");
