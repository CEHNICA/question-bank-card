"use strict";
const assert = require("node:assert/strict");
const layout = require("./exam-layout.js");
assert.deepEqual(layout.normalize(), { pagination: "compact", option_layout: "auto", option_overrides: {}, answer_space: "none", answer_space_overrides: {}, question_breaks: [] });
assert.deepEqual(layout.normalize({ pagination: "keep", option_layout: "four", option_overrides: { a: "two", b: "no" }, question_breaks: ["a", "a", 3] }),
  { pagination: "keep", option_layout: "four", option_overrides: { a: "two" }, answer_space: "none", answer_space_overrides: {}, question_breaks: ["a"] });
assert.equal(layout.keepWhole(900, layout.BODY_HEIGHT, "compact"), false);
assert.equal(layout.keepWhole(900, layout.BODY_HEIGHT, "keep"), true);
assert.equal(layout.keepWhole(1200, layout.BODY_HEIGHT, "keep"), false, "An oversized question must be allowed to continue instead of creating an empty sheet");
assert.equal(layout.requestedColumns("four", 4), 4);
assert.equal(layout.requestedColumns("four", 5), 2, "Five real options must survive a four-column preference");
assert.equal(layout.requestedColumns("two", 4), 2);
assert.equal(layout.requestedColumns("auto", 4), 0);
assert.equal(layout.requestedColumns("vertical", 4), 1);
assert.equal(layout.requestedColumns("vertical", 5), 1, "A vertical layout retains a genuine fifth option");
assert.equal(layout.normalize({ option_layout: "vertical", option_overrides: { a: "vertical" }, answer_space: "small", answer_space_overrides: { a: "large", b: "none", invalid: "custom" } }).option_overrides.a, "vertical");
assert.deepEqual(layout.normalize({ answer_space_overrides: { a: "large", b: "none", invalid: "custom" } }).answer_space_overrides, { a: "large", b: "none" });
for (const [mode, mm] of [["none", 0], ["small", 12], ["medium", 30], ["large", 60]]) assert.equal(layout.answerSpaceMm(mode), mm);
assert.equal(layout.answerSpace("free_response", "a", { document: "questions", answer_space: "small" }), "small");
assert.equal(layout.answerSpace("free_response", "a", { document: "questions", answer_space: "small", answer_space_overrides: { a: "none" } }), "none");
assert.equal(layout.answerSpace("free_response", "a", { document: "combined", answer_layout: "appendix", answer_space_overrides: { a: "large" } }), "large");
for (const mode of [{ document: "answers" }, { document: "combined", answer_layout: "inline" }]) {
  assert.equal(layout.answerSpace("free_response", "a", { ...mode, answer_space: "large", answer_space_overrides: { a: "medium" } }), "none", "Answers and inline teacher papers cannot acquire student space via an override");
}
assert.equal(layout.answerSpace("single_choice", "a", { document: "questions", answer_space: "large" }), "none");
assert.equal(layout.answerSpace("free_response", "a", {}), "none", "Legacy drafts without a choice remain unchanged");
for (const [first, expected] of [
  [{ nodeType: 3, textContent: "如图，求 " }, "inline"],
  [{ nodeType: 1, matches: () => false }, "inline"],
  [{ nodeType: 1, matches: () => true }, "block"]
]) {
  const stem = { dataset: {}, querySelector: () => ({ childNodes: [{ nodeType: 3, textContent: "  " }, first] }) };
  layout.alignQuestionNumber({ querySelector: () => stem });
  assert.equal(stem.dataset.examFirstLine, expected);
}
assert(Math.abs(layout.BODY_HEIGHT - 986.45669) < .001);
assert(Math.abs(layout.BODY_WIDTH - 672.755906) < .001);
assert.rejects(layout.paginate(null), /缺少/).then(() => console.log("A4 layout options and pagination boundaries: OK"));

// Real DOM Range can flatten several body paragraphs when a fragment starts
// inside one paragraph and ends inside another. Keep them in the body cell.
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "exam-layout.js"), "utf8");
class DomNode {
  constructor(tag, doc) { this.tagName = tag.toUpperCase(); this.nodeType = 1; this.ownerDocument = doc; this.childNodes = []; this.dataset = {}; this.className = ""; this.classList = { add: value => { this.className += ` ${value}`; } }; }
  get children() { return this.childNodes.filter(child => child.nodeType === 1); }
  get firstElementChild() { return this.children[0]; }
  get lastElementChild() { return this.children.at(-1); }
  append(...children) { for (const child of children) { if (child.nodeType === 11) this.append(...child.childNodes); else { child.parentNode = this; this.childNodes.push(child); } } }
  prepend(...children) { this.childNodes.unshift(...children); }
  matches(selector) { return selector.split(",").some(value => value.trim().startsWith(".") ? this.className.split(/\s+/).includes(value.trim().slice(1)) : this.tagName === value.trim().toUpperCase()); }
  querySelector(selector) { return this.children.find(child => child.matches(selector)) || this.children.map(child => child.querySelector(selector)).find(Boolean) || null; }
  querySelectorAll() { return []; }
  cloneNode(deep) { const result = new DomNode(this.tagName, this.ownerDocument); result.className = this.className; result.dataset = { ...this.dataset }; if (deep) result.append(...this.childNodes.map(child => child.nodeType === 3 ? { ...child } : child.cloneNode(true))); return result; }
  get textContent() { return this.childNodes.map(child => child.textContent).join(""); }
  set textContent(value) { this.childNodes = [{ nodeType: 3, textContent: value }]; }
}
const doc = { createElement: tag => new DomNode(tag, doc), createRange: () => ({ setStart() {}, setEnd() {}, cloneContents: () => ({ nodeType: 11, childNodes: [first.cloneNode(true), second.cloneNode(true)] }) }) };
const row = new DomNode("div", doc); row.className = "print-answer-row";
const label = new DomNode("strong", doc); label.textContent = "1.";
const body = new DomNode("div", doc); const first = new DomNode("div", doc), second = new DomNode("div", doc);
first.className = second.className = "qb-analysis"; first.textContent = "第 4 步完整横排正文"; second.textContent = "第 5 步完整横排正文";
body.append(first, second); row.append(label, body);
const context = { element: (owner, tag, className, text) => { const value = owner.createElement(tag); value.className = className; value.textContent = text; return value; } };
vm.createContext(context); vm.runInContext(source.slice(source.indexOf("  function boundaries("), source.indexOf("  async function paginate(")), context);
const points = context.boundaries(row, 900);
assert.equal(points[0].node, body); assert.equal(points.at(-1).node, body, "Answer splitting must stay inside the wide body cell");
for (const continued of [false, true]) {
  const part = context.fragment(row, points, 0, points.length - 1, continued);
  assert.equal(part.children.length, 2, "Every fragment has exactly the number cell and the body cell");
  assert.equal(part.firstElementChild.tagName, "STRONG"); assert.equal(part.lastElementChild.tagName, "DIV");
  assert.equal(part.lastElementChild.children.length, 2, "Multiple paragraphs remain together in the wide column");
  assert.equal(part.lastElementChild.textContent, first.textContent + second.textContent);
}
console.log("Long answer fragments retain one full-width body cell and preserve every paragraph: OK");

// Explicit vertical must win over the renderer's compact default, including
// picture-only options and a fifth real choice; labels/content stay untouched.
const fit = { requestedColumns: layout.requestedColumns };
vm.runInNewContext(source.slice(source.indexOf("  function fitOptions("), source.indexOf("  // Character boundaries")), fit);
const optionNodes = ["A", "B", "C", "D", "E"].map(label => ({ label }));
const list = { children: optionNodes, dataset: { cols: "4" }, style: {},
  classList: { contains: () => true, remove() {}, add() {} }, querySelectorAll: () => [] };
const question = { dataset: { questionId: "a" }, querySelectorAll: () => [list] };
fit.fitOptions(question, layout.normalize({ option_layout: "two", option_overrides: { a: "vertical" } }), []);
assert.equal(list.dataset.examColumns, "1");
assert.equal(list.style.gridTemplateColumns, "repeat(1, minmax(0, 1fr))");
assert.deepEqual(list.children.map(option => option.label), ["A", "B", "C", "D", "E"]);
console.log("Explicit vertical option layout preserves all option nodes: OK");

// A preview/PDF fit is based on the final field width, clears a stale screen
// size, and reports an unfit formula instead of silently shrinking below 12px.
const fitted=[];
function formula(width, fieldWidth, fontSize) {
  const math={dataset:{},style:{removeProperty:()=>{},setProperty:(key,value)=>fitted.push([key,value])}};
  const html={children:[{getBoundingClientRect:()=>({width})}]};
  const span={querySelector:selector=>selector===".katex"?math:null,closest:()=>({clientWidth:fieldWidth})};
  math.querySelector=()=>html;
  const root={ownerDocument:{defaultView:{getComputedStyle:()=>({fontSize:String(fontSize)})}},querySelectorAll:selector=>selector===".katex"?[math]:[span]};
  return {root,math};
}
let sample=formula(700,674,18);
assert.equal(layout.fitFormulas(sample.root),0);assert.equal(fitted.at(-1)[0],"--print-math-size");
assert(Math.abs(parseFloat(fitted.at(-1)[1])-18*668/700)<.0001);
sample=formula(1800,674,18);assert.equal(layout.fitFormulas(sample.root),1);assert.equal(sample.math.dataset.examMathOverflow,"1");
sample=formula(100,674,18);assert.equal(layout.fitFormulas(sample.root),0);
console.log("Shared A4 formula fit retains readable minimum width and reports overflow: OK");
