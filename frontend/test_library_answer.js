"use strict";

// 1.10.5: the library no longer opens every answer at once.  Each question has
// its own “看答案” that opens only that question (the book has hundreds).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const QB = require("./qb-render.js");
const vm = require("node:vm");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");

// No switch that opens every answer.
assert.doesNotMatch(html, /id="answerToggle"/);
assert.doesNotMatch(js, /ui\.answers/);
assert.match(js, /QB\.renderQuestion\(paper, item\.content, \{ showNumber: false, showAnswer: "none" \}\);/);
// One toggle per question, holding the paper's answer and the AI's (when switched on).
const reveal = js.slice(js.indexOf("function answerReveal(item)"), js.indexOf("function extrasNode(item)"));
assert.match(reveal, /if \(!original && !ai && !saved\) return null;/);
assert.match(reveal, /node\("span", "when-closed", "看答案"\), node\("span", "when-open", "收起答案"\)/);
assert.match(reveal, /QB\.answerRows\(document, content\)/);
assert.match(reveal, /"AI 参考答案 · 未核对"/);
// The saved solution renders through the shared renderer, with the screen-only
// sub-question display layer switched on. The print path must stay off it.
assert.match(reveal, /solutions\.render\(body, saved, \{ node, QB, subQuestions: true \}\)/);
const printRow = js.slice(js.indexOf("function printAnswerRow"), js.indexOf("function printAnswerRow") + 900);
assert.doesNotMatch(printRow, /subQuestions/, "the print/PDF path keeps its current pagination");
// What is open stays open when the list redraws.
assert.match(reveal, /box\.open = state\.opened\.has\(item\.id\);/);
assert.match(js, /state\.opened\.has\(item\.id\), state\.tag\]\);/);
assert.match(css, /\.library-answer\[open\] \.when-open \{ display: inline; \}/);
// The answer rows are shared with the review page's 答案与解析.
assert.equal(typeof QB.answerRows, "function");

// Exercise the actual card projection: saving an edit makes it the visible
// answer, while the source and AI draft remain available in a closed history.
function element(tag, className = "", text = "") {
  return { tag, className, ownText: text || "", children: [], open: false,
    append(...values) { this.children.push(...values); }, addEventListener() {},
    get textContent() { return this.ownText + this.children.map(value => value.textContent || "").join(""); } };
}
const fixture = { id: "q", content: { answer: "source", analysis: "source steps" },
  solution: { answer: "edited", analysis: "edited steps" }, ai_answer: { answer: "old AI", analysis: "AI steps" } };
const before = JSON.stringify(fixture);
const model = { features: { ai_answer: true }, opened: new Set(["q"]) };
const context = { state: model, node: element, document: {},
  QB: { answerRows(_document, value) { return [element("p", "", value.answer), element("p", "", value.analysis)]; } },
  solutions: { hasContent: value => Boolean(value.answer || value.analysis), render(container, value) { container.append(element("p", "", value.answer + " " + value.analysis)); } } };
vm.createContext(context); vm.runInContext(reveal + "\nthis.show = answerReveal;", context);
const result = context.show(fixture), history = result.children.find(value => value.className === "library-answer-history");
assert(history); assert.equal(history.open, false); assert.equal(result.open, true);
assert(history.textContent.includes("old AI")); assert(history.textContent.includes("source steps"));
const primary = result.children.filter(value => value !== history).map(value => value.textContent).join("");
assert(primary.includes("edited steps")); assert(!primary.includes("old AI")); assert(!primary.includes("source steps"));
assert.equal(JSON.stringify(fixture), before, "Display never deletes or rewrites historical answers");
const draftOnly = context.show({ ...fixture, solution: null, content: {} });
assert(draftOnly.textContent.includes("old AI")); assert(!draftOnly.children.some(value => value.className === "library-answer-history"));
model.features.ai_answer = false;
assert(!context.show(fixture).textContent.includes("old AI"));
assert.equal(context.show({ id: "empty", content: {} }), null);

console.log("library answer checks: OK");
