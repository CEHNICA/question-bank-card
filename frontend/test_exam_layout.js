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
const measured = new WeakMap();
const context = { element: (owner, tag, className, text) => { const value = owner.createElement(tag); value.className = className; value.textContent = text; return value; },
  outerHeight: (node) => measured.get(node) || 0 };
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

// A figure row that fills a page must still be able to move on: the real sample is
// 九上第 227 题, a row of six figures measuring 985px on a 986px page. Treating
// the row as one unbreakable block leaves the binary search with nothing that
// fits, and the whole paper export fails. Each figure stays whole; the row may
// break between them.
{
  const doc = { createElement: tag => new DomNode(tag, doc) };
  const question = new DomNode("div", doc); question.className = "print-question";
  const stem = new DomNode("div", doc); stem.className = "qb-stem";
  const body = new DomNode("span", doc); body.className = "qb-stem-body";
  body.append({ nodeType: 3, textContent: "如图，画出图象。" });
  stem.append(body);
  const row = new DomNode("div", doc); row.className = "qb-figures";
  const pictures = [new DomNode("figure", doc), new DomNode("figure", doc)];
  pictures.forEach(picture => { picture.className = "qb-figure"; measured.set(picture, 600); });
  row.append(...pictures);
  question.append(stem, row);
  measured.set(row, 1200);

  // boundaries() 在 vm 里跑，返回的数组原型属于那个 realm；assert/strict 的
  // deepEqual 会比原型，直接比必然红。先在本地 realm 取成普通数组再比。
  const rowOffsets = points => { const out = []; for (const point of Array.from(points)) if (point.node === row) out.push(point.offset); return out; };
  assert.deepEqual(rowOffsets(context.boundaries(question, 900)), [0, 1, 2],
    "两张图之间要留断点：图本身不拆，图与图之间可以分页，整组不能被当成一块");
  // 一组本来就放得下的小图也照旧只在图之间留断点——分不分页由放不放得下决定。
  measured.set(row, 400); measured.set(pictures[0], 200); measured.set(pictures[1], 200);
  assert.deepEqual(rowOffsets(context.boundaries(question, 900)), [0, 1, 2]);
  console.log("A figure row splits between its figures instead of failing the whole export: OK");
}

// 一整块图表常常「自己放得下、跟在它前面的文字就放不下」。没有块前断点时，
// 二分找不到任何能放下的片段，整卷导出直接失败。整块照旧不许拆，该分的是分页。
{
  const doc = { createElement: tag => new DomNode(tag, doc) };
  const question = new DomNode("div", doc); question.className = "print-question";
  const stem = new DomNode("div", doc); stem.className = "qb-stem";
  stem.append({ nodeType: 3, textContent: "如图，已知 AB = 5cm，请用量角器量出 ∠A 的度数，并写出做法。" });
  const picture = new DomNode("figure", doc); picture.className = "qb-figure"; measured.set(picture, 900);
  question.append(stem, picture);
  // 收尾断点和最后一条断点会重合（同一 node 同一 offset），对分页没影响，断言看去重后的。
  const offsetsOf = (points, owner) => [...new Set(Array.from(points, point => point.node === owner ? point.offset : -1))].filter(offset => offset >= 0);
  assert.deepEqual(offsetsOf(context.boundaries(question, 986), question), [0, 1, 2], "文字后面跟一张高图，图前面要能分页；图本身仍不许拆");
  console.log("A whole block keeps a break point in front of it: OK");
}

// 报错要指出是哪道题，否则老师只能一题题自己试。
{
  const doc = { createElement: tag => new DomNode(tag, doc) };
  const question = new DomNode("div", doc); question.className = "print-question"; question.dataset.questionId = "1150";
  const number = new DomNode("span", doc); number.className = "qb-number"; number.textContent = "227.";
  question.append(number);
  assert.equal(context.describe(question), "第 227 题", "报错要念出卷面上印的题号，不是数据库主键");
  const answerRow = new DomNode("div", doc); answerRow.className = "print-answer-row";
  assert.equal(context.describe(answerRow), "答题区");
  const heading = new DomNode("div", doc); heading.className = "print-section";
  assert.equal(context.describe(heading), "分区标题");
  // 只测 describe() 证明不了「报错真的用上了它」。两道「放不下」的出口都要点名，
  // 而且必须是紧跟在 throw new Error( 后面的模板字面量——挪到别处或注释里都不算数。
  const layoutThrows = source.match(/throw new Error\(`[^`]*(?:高于一页 A4|无法放进 A4)[^`]*`\)/g) || [];
  assert.equal(layoutThrows.length, 2, "两道放不下的报错出口各一处，少一处就有一道题还会报不出题号");
  for (const text of layoutThrows) assert.match(text, /throw new Error\(`\$\{describe\(node\)\}/,
    "报错要念出题号：${describe(node)} 必须紧跟在 throw new Error( 后面");
  console.log("Export failures name the question on paper: OK");
}

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

// A question that runs onto the next page has both page ends inside the same
// .qb-stem-body, and cloneContents() only returns what the range covers — the
// shared ancestors are dropped. The fragment must put them back: only
// .qb-stem-body carries white-space: pre-wrap, so a bare fragment turns every
// blank line in the stem into a space and the whole continuation reads as one
// paragraph.
{
  const covered = { nodeType: 3, textContent: "。∵ 第二步\n∴ 第三步\n" };
  const pageDoc = {
    createElement: tag => new DomNode(tag, pageDoc),
    createRange: () => ({
      startNode: null,
      setStart(node) { this.startNode = node; },
      setEnd(node) { this.endNode = node; },
      // Both ends sit in the same node, so the range covers part of it and its
      // shared ancestors: exactly what the browser hands back in this case.
      get commonAncestorContainer() { return this.startNode; },
      cloneContents: () => ({ nodeType: 11, childNodes: [{ nodeType: 3, textContent: covered.textContent.slice(1) }] })
    })
  };
  const stemBody = new DomNode("span", pageDoc); stemBody.className = "qb-stem-body";
  const stem = new DomNode("div", pageDoc); stem.className = "qb-stem";
  const question = new DomNode("div", pageDoc); question.className = "print-question";
  stemBody.append(covered); stem.append(stemBody); question.append(stem);
  DomNode.prototype.contains = function (node) { for (let up = node; up; up = up.parentNode) { if (up === this) return true; } return false; };

  const points = [{ node: covered, offset: 0 }, { node: covered, offset: covered.textContent.length }];
  const continued = context.fragment(question, points, 1, 1, true);
  const wrapped = continued.querySelector(".qb-stem-body");
  assert.ok(wrapped, "续页仍要有 .qb-stem-body，否则题面里的空行会被折成空格");
  assert.equal(wrapped.className, "qb-stem-body");
  assert.equal(wrapped.textContent, "∵ 第二步\n∴ 第三步\n", "题面文字一个都不能少");
  assert.equal(continued.querySelector(".qb-stem").className, "qb-stem");
  // 续页仍要带着题号出现。
  const continued2 = context.fragment(question, points, 1, 1, true);
  assert.ok(continued2.querySelector(".qb-stem").querySelector(".qb-number").textContent.includes("（续）"));

  // 起止都还在题目这一层时（首页片段），不额外套壳。
  const whole = { node: question, offset: 0 };
  const first = context.fragment(question, [whole, whole], 0, 1, false);
  assert.equal(first.children.length, 0, "首页片段直接放内容，不多套一层");
  assert.equal(first.childNodes.length, 1);
  console.log("Continued pages keep the stem body, its blank lines and the question number: OK");
}
