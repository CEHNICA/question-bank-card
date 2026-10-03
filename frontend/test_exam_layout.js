"use strict";
const assert = require("node:assert/strict");
const layout = require("./exam-layout.js");
assert.deepEqual(layout.normalize(), { pagination: "compact", option_layout: "auto", option_overrides: {}, question_breaks: [] });
assert.deepEqual(layout.normalize({ pagination: "keep", option_layout: "four", option_overrides: { a: "two", b: "no" }, question_breaks: ["a", "a", 3] }),
  { pagination: "keep", option_layout: "four", option_overrides: { a: "two" }, question_breaks: ["a"] });
assert.equal(layout.keepWhole(900, layout.BODY_HEIGHT, "compact"), false);
assert.equal(layout.keepWhole(900, layout.BODY_HEIGHT, "keep"), true);
assert.equal(layout.keepWhole(1200, layout.BODY_HEIGHT, "keep"), false, "An oversized question must be allowed to continue instead of creating an empty sheet");
assert.equal(layout.requestedColumns("four", 4), 4);
assert.equal(layout.requestedColumns("four", 5), 2, "Five real options must survive a four-column preference");
assert.equal(layout.requestedColumns("two", 4), 2);
assert.equal(layout.requestedColumns("auto", 4), 0);
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
