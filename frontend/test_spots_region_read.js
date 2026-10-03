"use strict";

// 1.10.2: the spots “MinerU 读法不同” names are numbered on the crop and in the
// flag, and the character is coloured inside its formula; 框选识读 reads one
// region of the paper on its own and fills an option through 改字.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const QB = require("./qb-render.js");
const katex = require("./vendor/katex/katex.min.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// ---- the disputed character, coloured inside its formula
const stem = "（1）当 $x>0$ 时，求 $y=2x+\\dfrac{1}{x^{3}}$ 最小值；";
const at = stem.indexOf("{3}") + 1;
const segment = QB.typesetSegments(stem).find((part) => part.type === "math" && part.start <= at && part.end > at);
const coloured = QB.colourLatex(stem, segment, { start: at, end: at + 1, exact: true });
assert.equal(coloured, "y=2x+\\dfrac{1}{x^{{\\textcolor{#c2410c}{3}}}}");
assert.doesNotThrow(() => katex.renderToString(coloured, { throwOnError: true }));
// x^3 without braces still makes valid LaTeX.
const bare = "$x^3+1$";
const bareSegment = QB.typesetSegments(bare)[0];
const bareColoured = QB.colourLatex(bare, bareSegment, { start: 3, end: 4, exact: true });
assert.equal(bareColoured, "x^{\\textcolor{#c2410c}{3}}+1");
assert.doesNotThrow(() => katex.renderToString(bareColoured, { throwOnError: true }));
// Never inside a command name or across a brace: the whole formula is boxed instead.
const command = "$\\infty$";
assert.equal(QB.colourLatex(command, QB.typesetSegments(command)[0], { start: 3, end: 4, exact: true }), null);
const brace = "$x^{3}$";
assert.equal(QB.colourLatex(brace, QB.typesetSegments(brace)[0], { start: 3, end: 5, exact: true }), null);

// ---- a mark in prose whose spaces and line breaks were tidied marks the same characters
const prose = "某商品的销售 定价为 a 元，\n求利润";
const tidied = QB.tidyText(prose);
assert.equal(tidied, "某商品的销售定价为a元，求利润");
const ding = prose.indexOf("定");
assert.deepEqual(QB.tidiedMarks(prose, tidied, 0, [{ start: ding, end: ding + 1, kind: "spot" }]),
  [{ start: tidied.indexOf("定"), end: tidied.indexOf("定") + 1, kind: "spot" }]);
// A mark over spaces alone cannot be placed: the caller marks the whole run.
assert.equal(QB.tidiedMarks(prose, tidied, 0, [{ start: 6, end: 7 }]), null);
assert.match(QB.tidiedMarks.toString(), /text\[j\] !== raw\[i\]\) return null/);

// ---- spots on the card
assert.match(js, /function cropView\(regions, \{ figures = \[\], spots = \[\], onZoom, capToNatural = false \} = \{\}\)/);
assert.match(js, /cropView\(q\.regions, \{ figures: q\.figures, spots: q\.check_spots,/);
assert.match(js, /cropView\(regions, \{ figures: q\.figures \|\| \[\], spots: q\.check_spots \}\)/);
assert.match(js, /const box = el\("span", `crop-spot\$\{\(Math\.max\(sy0, y0\) - y0\) \/ rh < 0\.15 \? " label-below" : ""\}`\);/);
// One box per line, labelled with every number in it.
assert.match(js, /box\.numbers\.join\(""\)/);
assert.match(js, /marks: reviewMarks\(q\)/);
assert.doesNotMatch(js, /marks: diffMarks\(q\), showAnswer/);
assert.match(js, /kind: "spot", exact: true/);
// The flag numbers its spots like the boxes.
assert.match(js, /const SPOT_FLAG = \/\^\(\(\?:两次识读一致\|第三次识读裁决后\)/);
assert.match(js, /el\("span", "flag-spot-number", spotNumber\(index \+ 1\)\)/);
assert.match(css, /\.crop-spot \{ position: absolute; border: 2px dashed #c2410c;/);
assert.match(css, /mark\.qb-mark\.spot \{/);

// ---- 框选识读
assert.match(js, /button\("框选识读", "", \(\) => openPageDialog\("read", q\)/);
assert.match(html, /<label id="readTargetField" class="number-field" hidden>读出来的字填到/);
assert.match(html, /<option value="A">选项 A<\/option>/);
assert.match(js, /\$\("readTargetField"\)\.hidden = mode !== "read";/);
assert.match(js, /\$\("pageDialogSave"\)\.textContent = mode === "read" \? "识读这一块" : mode === "new" \? "保存原图题" : "保存";/);
// One box: a new one replaces the old.
assert.match(js, /if \(dialog\.mode === "read"\) dialog\.boxes = \[\];/);
assert.match(js, /api\(`\/api\/questions\/\$\{q\.id\}\/region-read`, \{\s*method: "POST", body: \{ page_idx: box\.page_idx, bbox: box\.bbox, target \}/);
// The page keeps polling while a region is read, and the result fills an option through 改字.
assert.match(js, /q\.state === "reading" \|\| q\.ocr_pending \|\| regionReadPending\(q\)/);
assert.match(js, /openEditor\(card, q, \{ prefill: \{ field: target, value: read\.text \} \}\)/);
assert.match(js, /function openEditor\(card, q, \{ prefill = null \} = \{\}\)/);
assert.match(js, /prefilled\.classList\.add\("prefilled"\);/);
assert.match(js, /method: "DELETE", body: \{\}/);
// Without a clipboard (an older browser) it says so instead of failing silently.
assert.match(js, /: Promise\.reject\(new Error\("clipboard unavailable"\)\);/);
// New reads offer AI positioning while an explicitly chosen target is retained.
const guess = js.slice(js.indexOf("function readTargetGuess(q)"), js.indexOf("function regionReadPending(q)"));
assert.match(guess, /return "auto"/);
assert.match(html, /<option value="auto">自动推荐（AI）<\/option>/);
assert.match(js, /确认位置并填入改字/);

console.log("spots / region read checks: OK");
