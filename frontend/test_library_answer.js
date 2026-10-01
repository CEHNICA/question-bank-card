"use strict";

// 1.10.5: the library no longer opens every answer at once.  Each question has
// its own “看答案” that opens only that question (the book has hundreds).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const QB = require("./qb-render.js");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");

// No switch that opens every answer.
assert.doesNotMatch(html, /id="answerToggle"/);
assert.doesNotMatch(js, /ui\.answers/);
assert.match(js, /QB\.renderQuestion\(paper, item\.content, \{ showNumber: false, showAnswer: "none" \}\);/);
// One toggle per question, holding the paper's answer and the AI's (when switched on).
const reveal = js.slice(js.indexOf("function answerReveal(item)"), js.indexOf("function extrasNode(item)"));
assert.match(reveal, /if \(!original && !ai\) return null;/);
assert.match(reveal, /node\("span", "when-closed", "看答案"\), node\("span", "when-open", "收起答案"\)/);
assert.match(reveal, /QB\.answerRows\(document, content\)/);
assert.match(reveal, /"AI 参考答案 · 未核对"/);
// What is open stays open when the list redraws.
assert.match(reveal, /box\.open = state\.opened\.has\(item\.id\);/);
assert.match(js, /state\.opened\.has\(item\.id\), state\.tag\]\);/);
assert.match(css, /\.library-answer\[open\] \.when-open \{ display: inline; \}/);
// The answer rows are shared with the review page's 答案与解析.
assert.equal(typeof QB.answerRows, "function");

console.log("library answer checks: OK");
