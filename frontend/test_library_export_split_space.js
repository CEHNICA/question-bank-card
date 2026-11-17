"use strict";
// 第三十三轮：分卷导出（分别导出题目卷与答案卷）必须和单独导出拿到同一份题目卷。
//
// 病根：currentPrintOptions() 把「合卷＋逐题紧跟 ⇒ 留白归零」这条**渲染期**规则
// 提前烘焙进了发给后端的 payload。于是「分别导出题目卷与答案卷」里那份题目卷
// 明明没有答案紧跟（答案在另一份卷子上），答题留白却被压成 0 ——
// 同一份题目卷，答题空间取决于一个跟它无关的下拉值，少了 22 毫米。
//
// 规矩：payload 只送「用户设的值」，归零判断交给渲染端自己判
// （预览 exam-layout.js、PDF library_pdf.py、Word _document 三边各判一次）。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(
  process.env.QB_LIB_JS ? path.resolve(process.env.QB_LIB_JS) : path.join(__dirname, "library.js"), "utf8");
// 看门狗要能喂「回退副本」自证这条守护不是摆设，所以渲染端那一半也留了入口。
const layout = process.env.QB_EXAM_LAYOUT_JS
  ? require(path.resolve(process.env.QB_EXAM_LAYOUT_JS))
  : require("./exam-layout.js");

const controls = Object.fromEntries([
  "printDocument", "printAnswerLayout", "printPagination", "printOptionLayout", "printFontSize",
  "printAnswerSpace", "printStudentInfo"
].map(id => [id, { value: "", checked: false, disabled: false }]));
const ui = { printAnswers: {}, printOrigin: { checked: false }, printAi: { checked: false },
  paper: { querySelectorAll: () => [], querySelector: () => null } };
const state = { features: { ai_answer: false }, basket: [] };
const printState = { optionOverrides: {}, answerSpaceOverrides: {}, figureOverrides: {}, questionBreaks: [] };
const sandbox = {
  $: id => controls[id], ui, state, printState, printAnswersPreference: true,
  document: { fonts: { ready: Promise.resolve() } }, setTimeout, clearTimeout,
  CSS: { escape: value => value }
};
vm.createContext(sandbox);
vm.runInContext(
  source.slice(source.indexOf("  function normalizePrintOptions("), source.indexOf("  function printAnswerContent(")),
  sandbox
);

// 一、合卷＋逐题紧跟：payload 里仍然是用户设的留白值，不是 0。
controls.printDocument.value = "combined";
controls.printAnswerLayout.value = "inline";
controls.printAnswerSpace.value = "auto";
assert.equal(sandbox.currentPrintOptions().answer_space, "auto",
  "A combined inline paper must still carry the teacher's own writing-space setting");
controls.printAnswerSpace.value = "60";
assert.equal(sandbox.currentPrintOptions().answer_space, 60,
  "A combined inline paper must carry the millimetre value the teacher picked");

// 二、归零判断仍然存在，只是搬到了渲染端（预览那一层）。
assert.equal(layout.answerSpace("free_response", "a", { document: "combined", answer_layout: "inline", answer_space: "auto" }), 0,
  "The preview still drops writing space when the answers follow every question");
assert.equal(layout.answerSpace("free_response", "a", { document: "questions", answer_space: "auto" }), 30,
  "A question sheet still keeps the smart 30 mm of writing space");

// 三、其它两种输出内容不受影响。
controls.printDocument.value = "questions";
controls.printAnswerLayout.value = "appendix";
assert.equal(sandbox.currentPrintOptions().answer_space, 60, "A question sheet carries the teacher's setting as it is");
controls.printDocument.value = "answers";
assert.equal(sandbox.currentPrintOptions().answer_space, 60, "An answer sheet carries the teacher's setting as it is");

// 四、静态守卫：currentPrintOptions 里不许再出现「inline ⇒ 归零」的表达式。
const body = source.slice(source.indexOf("  function currentPrintOptions("), source.indexOf("  function prunePrintChoices("));
assert.doesNotMatch(body, /answer_layout[^\n]*===\s*"inline"[^\n]*\?\s*0/,
  "currentPrintOptions must not decide writing space for the renderer; that rule belongs to the renderers");
assert.match(body, /answer_space:\s*normalizePrintSpace\(/,
  "currentPrintOptions sends the teacher's writing-space value unchanged");

// 五、草稿存的是同一份设置：回来以后还是老师选的那一档，不是被改成的 0。
controls.printDocument.value = "combined";
controls.printAnswerLayout.value = "inline";
controls.printAnswerSpace.value = "auto";
sandbox.applyPrintOptions({ document: "combined", answer_layout: "inline", answer_space: "auto" });
assert.equal(controls.printAnswerSpace.value, "auto",
  "A saved draft must come back with the teacher's own writing-space setting");
assert.equal(sandbox.currentPrintOptions().answer_space, "auto",
  "A restored draft must not turn the writing space into zero");
