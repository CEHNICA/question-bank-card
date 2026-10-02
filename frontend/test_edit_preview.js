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
// Side by side: under the original crop in the left column; otherwise under the stem box.
assert.match(js, /if \(beside && previewBox\.parentNode !== source\) source\.append\(previewBox\);/);
assert.match(js, /else if \(!beside && previewBox\.previousElementSibling !== stemRow\) stemRow\.after\(previewBox\);/);
assert.match(js, /editor\.append\(title, typeRow, originRow, stemRow, previewBox, tableTools, optionBox, extra, bar\);/);
assert.match(css, /\.editor-preview-box\.beside \.editor-preview \{ max-height: var\(--preview-room\); overflow: auto;/);
// The stem box grows with its text so the preview under it stays in sight.
assert.match(js, /stem\.style\.height = `\$\{Math\.min\(stem\.scrollHeight \+ 2, Math\.round\(window\.innerHeight \* 0\.4\)\)\}px`;/);
// Closing the editor takes the preview (which may live in the left column) with it.
assert.match(js, /cancelAnimationFrame\(frame\);[\s\S]*?window\.removeEventListener\("resize", relayout\);\s*previewBox\.remove\(\);/);

console.log("edit preview checks: OK");
