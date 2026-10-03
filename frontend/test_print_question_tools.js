"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync(require.resolve("./library.js"), "utf8");
const html = fs.readFileSync(require.resolve("./library.html"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");
class Node {
  constructor(tag, className = "", text = "") { this.tagName = tag; this.className = className; this.textContent = text; this.children = []; this.attributes = {}; this.dataset = {}; this.events = {}; this.isConnected = true; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, callback) { this.events[type] = callback; }
}
const controls = { printDocument: { value: "questions" }, printAnswerLayout: { value: "inline" }, closePrint: { focus() {} } };
const item = { id: "a", question_type: "free_response", content: { options: { A: "1", B: "2", C: "3", D: "4" } } };
let rendered = 0, dirty = 0, answerCalls = 0;
const context = {
  node: (...args) => new Node(...args), $: id => controls[id], state: { basket: ["a"] },
  printState: { exporting: false, optionOverrides: {}, answerSpaceOverrides: {}, questionBreaks: [], items: [item], firstQuestion: "a", activeToolId: null },
  ui: { paper: { querySelectorAll: () => [] } }, markDraftDirty: () => dirty++, renderPrint: () => rendered++,
  openAnswerEditor: () => answerCalls++, groupedQuestionNumber: () => 1
};
vm.runInNewContext(source.slice(source.indexOf("  function printTools("), source.indexOf("  async function waitForPrintAssets(")), context);
const tool = context.printTools([item], [item], 0, 1);
assert.equal(tool.tagName, "details"); assert.equal(tool.open, false);
assert.equal(tool.children[0].tagName, "summary"); assert.equal(tool.children[0].textContent, "调整");
assert.equal(tool.children[0].attributes["aria-label"], "第 1 题排版操作");
const actions = tool.children[1], [up, down, remove, options, space, page, answer] = actions.children;
assert.equal(actions.attributes.role, "group");
assert.equal(up.disabled, true); assert.equal(down.disabled, true); assert.equal(page.disabled, true);
assert.equal(options.children.some(value => value.value === "vertical"), true);
assert.equal(space.hidden, false); assert.equal(space.disabled, false);
assert.equal(space.children.find(value => value.value === "small").textContent, "少量（12 毫米）");
options.value = "vertical"; options.events.change();
assert.equal(context.printState.optionOverrides.a, "vertical");
space.value = "none"; space.events.change();
assert.equal(context.printState.answerSpaceOverrides.a, "none", "A single question can explicitly opt out of the global default");
space.value = ""; space.events.change(); assert.equal(Object.hasOwn(context.printState.answerSpaceOverrides, "a"), false);
assert.equal(dirty, 3); assert.equal(rendered, 3);
answer.events.click(); assert.equal(answerCalls, 1);
tool.open = true; tool.events.toggle(); assert.equal(context.printState.activeToolId, "a");
assert.equal(context.printTools([item], [item], 0, 1).open, true, "After changing layout the same question's controls remain accessible");
tool.open = false; tool.events.toggle(); assert.equal(context.printState.activeToolId, null);
controls.printDocument.value = "combined";
assert.equal(context.printTools([item], [item], 0, 1).children[1].children[4].disabled, true);
controls.printDocument.value = "questions"; context.printState.exporting = true;
const locked = context.printTools([item], [item], 0, 1).children[1].children;
assert.equal(locked.every(value => value.disabled), true, "Export locks every mutable action, including space and option choices");
context.printState.exporting = false; item.question_type = "single_choice";
assert.equal(context.printTools([item], [item], 0, 1).children[1].children[4].hidden, true, "Writing space is only for free-response questions");
assert.match(html, /id="printAnswerSpace"[\s\S]*?value="small" selected/);
for (const id of ["printOptionLayout", "printIndividualOption"]) assert.match(html, new RegExp(`id="${id}"[^]*?value="vertical"`));
assert.match(css, /\.print-question-actions \{[^}]*background: #fff;/);
assert.match(css, /\.print-question-tools > summary \{[^}]*background: #fff;/);
assert.match(css, /\.print-question-tools button:disabled[^}]*opacity: 1;/);
assert.doesNotMatch(css, /\.print-question-tools \{[^}]*(rgba|opacity: 0)/);
assert.match(css, /\.print-flow \.qb-stem, \.exam-page \.qb-stem \{ align-items: baseline;/);
assert.match(css, /\.print-answer-space\[data-answer-space="medium"\] \{ height: 30mm;/);
assert.match(css, /\.print-answer-space\[data-answer-space="large"\] \{ height: 60mm;/);
console.log("Print tools: short keyboard/touch disclosure, opaque controls, same-question persistence, vertical/space overrides and export locking: OK");
