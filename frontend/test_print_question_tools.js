"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync(require.resolve("./library.js"), "utf8");
const html = fs.readFileSync(require.resolve("./library.html"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");
class Node {
  constructor(tag, className = "", text = "") { this.tagName = tag; this.className = className; this.textContent = text; this.children = []; this.attributes = {}; this.dataset = {}; this.events = {}; this.isConnected = true; this.parent = null; this.rect = { top: 0, bottom: 0, left: 0, right: 0, height: 0, width: 0 }; this._classes = new Set(className ? className.split(/\s+/) : []); }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, callback) { this.events[type] = callback; }
  get classList() { return { add: name => this._classes.add(name), remove: name => this._classes.delete(name), contains: name => this._classes.has(name) }; }
  matches(selector) { return selector.startsWith(".") ? this._classes.has(selector.slice(1)) : this.tagName.toLowerCase() === selector.toLowerCase(); }
  closest(selector) { for (let el = this; el; el = el.parent) if (el.matches(selector)) return el; return null; }
  querySelector(selector) { for (const el of this.descendants()) if (el.matches(selector)) return el; return null; }
  *descendants() { for (const child of this.children) { yield child; yield* child.descendants(); } }
  contains(other) { for (let el = other; el; el = el.parent) if (el === this) return true; return false; }
  getBoundingClientRect() { return this.rect; }
}
const controls = { printDocument: { value: "questions" }, printAnswerLayout: { value: "inline" }, closePrint: { focus() {} } };
const item = { id: "a", question_type: "free_response", content: { options: { A: "1", B: "2", C: "3", D: "4" } } };
let rendered = 0, dirty = 0, answerCalls = 0;
const context = {
  node: (...args) => new Node(...args), $: id => controls[id], state: { basket: ["a"] }, window: { innerHeight: 1122 },
  printState: { exporting: false, optionOverrides: {}, answerSpaceOverrides: {}, questionBreaks: [], items: [item], firstQuestion: "a", activeToolId: null },
  ui: { paper: { querySelectorAll: () => [] } }, markDraftDirty: () => dirty++, renderPrint: () => rendered++,
  openAnswerEditor: () => answerCalls++, groupedQuestionNumber: () => 1
};
vm.runInNewContext(source.slice(source.indexOf("  function printTools("), source.indexOf("  async function waitForPrintAssets(")), context);
const tool = context.printTools([item], [item], 0, 1);
assert.equal(tool.tagName, "details"); assert.equal(tool.open, false);
assert.equal(tool.children[0].tagName, "summary"); assert.equal(tool.children[0].textContent, "调整");
assert.equal(tool.children[0].attributes["aria-label"], "第 1 题排版操作");
const actions = tool.children[1], [up, down, , options, space, page, answer] = actions.children;
const spaceInputOf = tools => tools.children[1].children[4].children[0].children[1];
const spacePresetsOf = tools => tools.children[1].children[4].children[1];
assert.equal(actions.attributes.role, "group");
assert.equal(up.disabled, true); assert.equal(down.disabled, true); assert.equal(page.disabled, true);
assert.equal(options.children.some(value => value.value === "vertical"), true);
assert.equal(space.className, "print-space-wrap");
assert.equal(spaceInputOf(tool).type, "number", "留白按毫米填，不是一档一档选");
assert.equal(spaceInputOf(tool).disabled, false);
assert.equal(spacePresetsOf(tool).children.length, 6, "跟随全卷 + 五个快捷档位");
options.value = "vertical"; options.events.change();
assert.equal(context.printState.optionOverrides.a, "vertical");
spaceInputOf(tool).value = "20"; spaceInputOf(tool).events.change();
assert.equal(context.printState.answerSpaceOverrides.a, 20, "A single question can take any millimetre value");
spacePresetsOf(tool).children[1].events.click();
assert.equal(context.printState.answerSpaceOverrides.a, 8, "A free-response question keeps an 8 mm floor even when the teacher asks for none");
spacePresetsOf(tool).children[0].events.click();
assert.equal(Object.hasOwn(context.printState.answerSpaceOverrides, "a"), false);
assert.equal(dirty, 4); assert.equal(rendered, 4);
answer.events.click(); assert.equal(answerCalls, 1);
tool.open = true; tool.events.toggle(); assert.equal(context.printState.activeToolId, "a");
assert.equal(context.printTools([item], [item], 0, 1).open, true, "After changing layout the same question's controls remain accessible");
tool.open = false; tool.events.toggle(); assert.equal(context.printState.activeToolId, null);
controls.printDocument.value = "combined";
assert.equal(spaceInputOf(context.printTools([item], [item], 0, 1)).disabled, true);
controls.printDocument.value = "questions"; context.printState.exporting = true;
const lockedTools = context.printTools([item], [item], 0, 1);
const locked = lockedTools.children[1].children;
assert.equal(locked.filter(value => value.disabled !== undefined).every(value => value.disabled), true, "Export locks every mutable action, including space and option choices");
assert.equal(spaceInputOf(lockedTools).disabled, true, "Export locks the writing-space box too");
context.printState.exporting = false; item.question_type = "single_choice";
const choiceTools = context.printTools([item], [item], 0, 1);
assert.equal(spaceInputOf(choiceTools).disabled, false, "Every question type can carry writing space");
assert.equal(spacePresetsOf(choiceTools).children.length, 6, "Choice questions get the same writing-space controls");
// 1.13.3: 「调整」菜单原本只往下展开。题排在纸的下部时它整个掉到纸外，
// 被后面那张 A4 盖死、点不动；纸本身被套了 transform，菜单提 z-index 也出不来。
const sheet = new Node("div", "exam-page");
sheet.rect = { top: 0, bottom: 1122, left: 0, right: 794, height: 1122, width: 794 };
sheet.append(context.printTools([item], [item], 0, 1));
const [lowTools] = sheet.children, lowActions = lowTools.children[1];
const box = (top, height) => ({ top, bottom: top + height, left: 0, right: 200, height, width: 200 });
lowActions.getBoundingClientRect = () => lowTools.classList.contains("opens-up") ? box(300, 200) : box(1000, 200);
lowTools.open = true; lowTools.events.toggle();
assert.equal(sheet.classList.contains("tools-open"), true, "The sheet is lifted above the following ones so its menu can be reached");
assert.equal(lowTools.classList.contains("opens-up"), true, "A question near the foot of the sheet flips the menu upwards instead of spilling onto the next sheet");
lowActions.getBoundingClientRect = () => box(100, 200);
lowTools.open = false; lowTools.events.toggle();
lowTools.open = true; lowTools.events.toggle();
assert.equal(lowTools.classList.contains("opens-up"), false, "A question with room below keeps the menu opening downwards");
assert.match(html, /id="printAnswerSpace"[\s\S]*?value="auto" selected/, "The global writing space defaults to the smart setting");
for (const id of ["printOptionLayout", "printIndividualOption"]) assert.match(html, new RegExp(`id="${id}"[^]*?value="vertical"`));
assert.match(css, /\.print-question-actions \{[^}]*background: #fff;/);
assert.match(css, /\.print-question-tools > summary \{[^}]*background: #fff;/);
assert.match(css, /\.print-question-tools button:disabled[^}]*opacity: 1;/);
assert.doesNotMatch(css, /\.print-question-tools \{[^}]*(rgba|opacity: 0)/);
assert.match(css, /\.print-flow \.qb-stem, \.exam-page \.qb-stem \{ align-items: baseline;/);
assert.match(css, /\.print-answer-space\[data-answer-space="medium"\] \{ height: 30mm;/);
assert.match(css, /\.print-answer-space\[data-answer-space="large"\] \{ height: 60mm;/);
console.log("Print tools: short keyboard/touch disclosure, opaque controls, same-question persistence, vertical/space overrides and export locking: OK");
