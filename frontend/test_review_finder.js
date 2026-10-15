"use strict";
const assert = require("node:assert/strict");
const F = require("./review-finder.js");

const questions = [
  { id: 1, number: 1, group: { id: 3, title: "第 3 组" }, stem: "求 sin x 的值", options: { A: "cos x" }, regions: [{ page_idx: 1, bbox: [0, 0, 10, 10] }] },
  { id: 2, number: 1, group: { id: 5, title: "第 5 组" }, stem: "求 \\sin x", options: { B: "COS x" }, regions: [{ page_idx: 2, bbox: [0, 0, 10, 10] }] },
  { id: 3, number: 9, group: { id: 5, title: "第 5 组" }, stem: "", options: {}, regions: [{ page_idx: 1, bbox: [0, 0, 10, 10] }] },
  { id: 4, number: 12, group: { id: 8, title: "第 8 组" }, stem: "题干有未识读的旧建议", ocr_suggestion: { stem: "sin 应不被搜到" }, regions: [] },
  { id: 5, number: 14, group: { id: 8, title: "第 8 组" }, stem: "旧文本", edited_stem: "人工改写 sin", regions: [] }
];

assert.deepEqual(F.search(questions, "sin").map((q) => q.id), [1, 2, 5], "plain, upper-case, LaTeX and manual text match");
assert.deepEqual(F.search(questions, "SIN").map((q) => q.id), [1, 2, 5]);
assert.deepEqual(F.search(questions, "\\sin").map((q) => q.id), [1, 2, 5]);
assert.deepEqual(F.search(questions, "第 1 题").map((q) => q.id), [1, 2], "same question number in different groups stays distinct");
assert.deepEqual(F.search(questions, "1").map((q) => q.id), [1, 2]);
assert.deepEqual(F.search(questions, "第 １ 题").map((q) => q.id), [1, 2], "full-width digits normalize");
assert.deepEqual(F.search(questions, "第 2 页").map((q) => q.id), [1, 3]);
assert.deepEqual(F.search(questions, "3 页").map((q) => q.id), [2]);
assert.deepEqual(F.search(questions, "  ").map((q) => q.id), []);
assert.deepEqual(F.search(questions, "未识读").map((q) => q.id), [4], "searches current saved text");
assert.deepEqual(F.search(questions, "应不被搜到").map((q) => q.id), [], "never searches OCR suggestions");
assert.deepEqual(F.search(questions, "人工改写").map((q) => q.id), [5], "manual saved text takes precedence");
assert.deepEqual(F.search(questions, "旧文本").map((q) => q.id), [], "obsolete text is not searched when a saved edit overrides it");
assert.match(F.snippet(questions[2]), /原图题/);
assert.equal(F.snippet({ stem: "甲乙丙丁" }, 2), "甲乙…");
const routes = require("node:fs").readFileSync(require.resolve("../backend/qb_server/urls.py"), "utf8");
assert.match(routes, /path\("review-finder\.js", views\._frontend\("review-finder\.js"/,
  "the browser serves the finder module before the main review script");
console.log("review finder: search and route checks passed");
