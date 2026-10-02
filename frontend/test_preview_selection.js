"use strict";

const assert = require("node:assert/strict");
const QB = require("./qb-render.js");
const Edits = require("./app.js");

// Offsets follow textarea UTF-16 positions even when display-only whitespace
// tidying removes spaces/newlines. No normalized string is written back.
assert.deepEqual(QB.previewTextOffsets("中文 文字", "中文文字"), [0, 1, 2, 2, 3, 4]);
assert.deepEqual(QB.previewTextOffsets("a   b", "a b"), [0, 1, 2, 2, 2, 3]);
assert.deepEqual(QB.previewTextOffsets("中文\n文字", "中文文字"), [0, 1, 2, 2, 3, 4]);
assert.deepEqual(QB.previewTextOffsets("甲😀乙", "甲😀乙"), [0, 1, 2, 3, 4]);
assert.deepEqual(QB.previewTextOffsets("（1）甲\n（2）乙", "（1）甲\n（2）乙"), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);

// Long formula source spans remain whole: a caret on the third denominator
// can find that formula without treating command letters as plain characters.
const formula = "已知 $\\frac{1}{x^2}+\\frac{2}{y^3}+\\frac{3}{z^4}=1$，求解。";
const at = formula.indexOf("z^4") + 2;
const active = QB.typesetSegments(formula).find((part) => part.start <= at && at < part.end);
assert.equal(active.type, "math");
assert.equal(formula.slice(active.start, active.end), "$\\frac{1}{x^2}+\\frac{2}{y^3}+\\frac{3}{z^4}=1$");
assert.equal(formula[at], "4");

// Table cell source offsets retain formula bars and escaped literal bars.
const tableText = "题目\n| 项目 | 值 |\n|---|---|\n| A | $|x|$ |\n| B | 甲\\|乙 |";
const table = QB.findTables(tableText)[0];
assert.equal(table.rows[1][1].text, "$|x|$");
assert.equal(tableText.slice(table.rows[2][1].start, table.rows[2][1].end), "甲\\|乙");
assert.equal(typeof QB.previewSelection, "function");

// A reviewed result only fills a form and selects its replacement; stale or
// malformed ranges must not replace a different part of the question.
const source = "甲😀乙 $x^2$ 丙";
const number = source.indexOf("2");
assert.deepEqual(Edits.reviewedPrefill(source, { value: "3", start: number, end: number + 1, before: "2" }),
  { value: "甲😀乙 $x^3$ 丙", start: number, end: number + 1 });
assert.equal(source, "甲😀乙 $x^2$ 丙");
assert.equal(Edits.reviewedPrefill(source, { value: "3", start: number, end: number + 1, before: "4" }), null);
assert.equal(Edits.reviewedPrefill(source, { value: "3", start: number, end: number + 1 }), null);
assert.equal(Edits.reviewedPrefill(source, { value: "字", start: 2, end: 3, before: source.slice(2, 3) }), null);
assert.equal(Edits.reviewedPrefill(source, { value: "字", start: -1, end: 1, before: "" }), null);
assert.equal(Edits.reviewedPrefill(source, { value: "字", start: 0, end: source.length + 1, before: source }), null);
assert.deepEqual(Edits.reviewedPrefill("", { value: "答案" }), { value: "答案", start: 0, end: 2 });
assert.equal(Edits.reviewedPrefill("旧答案", { value: "新答案", before: "另一道题" }), null);

console.log("preview selection source mapping checks: OK");
