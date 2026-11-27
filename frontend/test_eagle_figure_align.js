"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

assert.match(html, /id="eagleFigureAlignRow"/);
assert.match(html, /setQuestionFigureAlign\('left'\)/);
assert.match(html, /setQuestionFigureAlign\('center'\)/);
assert.match(html, /setQuestionFigureAlign\('right'\)/);
assert.match(html, /eagle_question_figure_align/);
assert.match(html, /class="my-3 eagle-q-figure"/);
assert.match(html, /html\[data-q-figure-align="center"\] \.eagle-q-figure \{ text-align: center; \}/);
assert.match(html, /html\[data-q-figure-align="left"\] \.qb-figures \{ justify-content: flex-start; \}/);
assert.doesNotMatch(html, /class="my-3 text-right"><img/);
console.log("题目配图位置：OK");
