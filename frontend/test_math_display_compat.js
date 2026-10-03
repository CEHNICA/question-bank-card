"use strict";
// Real local KaTeX parses the saved answer shape; no network or user data.
const assert = require("node:assert/strict");
const QB = require("./qb-render.js");
const katex = require("./vendor/katex/katex.min.js");
const S = require("./library-solutions.js");
const source = String.raw`$40^\circ+k\cdot180^\circ\（k\in\mathbb Z）$`;
const normalized = String.raw`40^\circ+k\cdot180^\circ(k\in\mathbb Z)`;
const options = { throwOnError: true, trust: false, strict: "ignore", maxSize: 10, maxExpand: 1000 };
const oneMath = value => {
  const segments = QB.typesetSegments(value);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].type, "math");
  assert.equal(value.slice(segments[0].start, segments[0].end), value, "Original offsets cover unchanged source");
  return segments[0];
};
const exact = oneMath(source);
assert.equal(exact.latex, normalized);
assert.doesNotThrow(() => katex.renderToString(exact.latex, options));
assert.throws(() => katex.renderToString(source.slice(1, -1), options), /Undefined control sequence/,
  "The fixture reproduces the original invalid escaped fullwidth bracket");
assert.equal(oneMath(String.raw`$（k\in\mathbb Z\）$`).latex, String.raw`(k\in\mathbb Z)`);
assert.equal(oneMath(String.raw`$40^\circ+k\cdot180^\circ\ (k\in\mathbb Z$`).latex,
  String.raw`40^\circ+k\cdot180^\circ\ (k\in\mathbb Z`, "A missing ordinary bracket remains missing");
assert.equal(oneMath(String.raw`$\（k\in\mathbb Z$`).latex, String.raw`(k\in\mathbb Z`,
  "Normalization never appends a mathematical condition or closing bracket");
assert.equal(oneMath(String.raw`$\{x\in\mathbb{Z}\}$`).latex, String.raw`\{x\in\mathbb{Z}\}`,
  "Existing valid escaped braces stay literal braces");
assert.equal(oneMath(String.raw`$\left\（x\right\）$`).latex, String.raw`\left(x\right)`,
  "Existing delimiter-sizing commands keep their meaning");
assert.doesNotThrow(() => katex.renderToString(oneMath(String.raw`$\left\（x\right\）$`).latex, options));
assert.equal(oneMath(String.raw`$\\（x）$`).latex, String.raw`\\(x)`, "An already escaped backslash is not consumed");
for (const malformed of [String.raw`$\mathbb{Z$`, String.raw`$\mathbbZ$`, String.raw`$\left(k\in\mathbb Z$`]) {
  const segment = oneMath(malformed);
  assert.equal(segment.latex, malformed.slice(1, -1));
  assert.throws(() => katex.renderToString(segment.latex, options), "Unrelated invalid formulas are never silently repaired");
}

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.style = {}; this.className = "";
    this.classList = { add: name => { this.className += ` ${name}`; } };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll() { return []; }
}
const document = { createElement: tag => new Element(tag), createTextNode: text => ({ nodeType: 3, nodeValue: text }) };
global.document = document;
global.katex = { ...katex, render: (latex, node, settings) => { node.innerHTML = katex.renderToString(latex, settings); } };
global.QBRender = QB;
const node = (tag, className = "", text) => {
  const result = document.createElement(tag); result.className = className;
  if (text !== undefined) result.append(document.createTextNode(text));
  return result;
};
const collect = host => [host, ...(host.children || []).flatMap(collect)];
const textOf = host => typeof host.textContent === "string" ? host.textContent : host.nodeType === 3 ? host.nodeValue : (host.children || []).map(textOf).join("");
const value = Object.freeze({ answer: source, analysis: `结果为 ${source}。` });
const literal = node("div"); QB.renderLiteral(literal, source);
assert.equal(textOf(literal), source, "The literal view still displays every saved backslash and fullwidth bracket");
const rows = QB.answerRows(document, value);
const maths = rows.flatMap(collect).filter(element => element.className?.split(" ").includes("qb-math"));
assert.equal(maths.length, 2, "Answer and analysis share the compatibility fix");
assert.ok(maths.every(element => element.innerHTML?.includes("katex")));
assert.ok(maths.every(element => element.dataset.raw === source), "Literal/raw view retains every original character");
const solution = node("div"); S.render(solution, value, { node, QB });
assert.equal(collect(solution).filter(element => element.className?.split(" ").includes("qb-math")).length, 2,
  "The full-screen/editor solution renderer uses the same rule");
assert.equal(QB.answerRows(document, { answer: "ACD" })[0].children[1].className, "qb-choice-answer");

const Export = require("./exam-export.js");
for (const format of ["docx", "pdf"]) {
  const item = Object.freeze({ id: "synthetic-math", content: Object.freeze({ stem: "计算角度。", answer: source, analysis: value.analysis }) });
  const fields = Export.serializeFields([item], { document: "combined" }, format)[item.id];
  assert.equal(fields.answer.source, source, "Export metadata preserves raw saved answer");
  assert.equal(fields.answer.blocks[0].segments[0].latex, normalized);
  assert.match(fields.answer.blocks[0].segments[0].mathml, /<math/);
  assert.equal(item.content.answer, source);
}
assert.equal(value.answer, source);
console.log("Escaped fullwidth round brackets: original failure, answer/analysis/solution display, PDF/Word MathML and source preservation: OK");
