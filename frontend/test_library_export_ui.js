"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

const controls = Object.fromEntries([
  "printDocument", "printPagination", "printOptionLayout", "printFontSize", "printAnswerSpace", "printStudentInfo", "printButton", "exportPdf", "exportWord", "exportSplit", "printIndividualQuestion", "printIndividualOption", "printIndividualBreak", "printIndividualHint"
].map(id => [id, { value: "", checked: false, disabled: false }]));
const ui = { printAnswers: {}, printOrigin: {}, printAi: {}, paper: { querySelectorAll: () => [] } };
const state = { features: { ai_answer: false } };
const printState = { items: [{}], missing: [], loading: false, exporting: false, availableAnswers: 0, tooWide: 0 };
const sandbox = { $: id => controls[id], ui, state, printState, printAnswersPreference: true, document: { fonts: { ready: Promise.resolve() } }, setTimeout, clearTimeout };
vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf("  function normalizePrintOptions("), source.indexOf("  function printAnswerContent(")), sandbox);
const plain = value => JSON.parse(JSON.stringify(value));

// Existing drafts retain answer intent, origin and explicitly chosen AI reference.
sandbox.applyPrintOptions({ answers: false, origin: true, ai_answers: true });
assert.equal(controls.printDocument.value, "questions");
assert.equal(ui.printOrigin.checked, true);
assert.equal(ui.printAi.checked, false, "A disabled AI feature must not silently enter an output");
assert.equal(controls.printFontSize.value, "12");
assert.equal(controls.printStudentInfo.checked, true);
sandbox.applyPrintOptions({ answers: true });
assert.equal(controls.printDocument.value, "combined");

state.features.ai_answer = true;
sandbox.applyPrintOptions({ document: "answers", answers: false, font_size: 16, answer_space: "large", student_info: false, ai_answers: true });
assert.deepEqual(plain(sandbox.currentPrintOptions()), { answers: true, origin: false, ai_answers: true, document: "answers", font_size: 16, answer_space: "large", student_info: false, pagination: "compact", option_layout: "auto", option_overrides: {}, question_breaks: [] });
assert.equal(sandbox.normalizePrintOptions({ font_size: 6, answer_space: "custom", document: "invalid" }).font_size, 12);
assert.equal(controls.printPagination.value, "compact", "Old drafts use the paper-saving default");
sandbox.applyPrintOptions({ pagination: "keep", option_layout: "four", option_overrides: { a: "two", b: "auto", bad: "invalid" }, question_breaks: ["b", "b"] });
state.basket = ["a", "b"];
assert.deepEqual(plain(sandbox.currentPrintOptions()).option_overrides, { a: "two", b: "auto" });
assert.deepEqual(plain(sandbox.currentPrintOptions()).question_breaks, ["b"]);
state.basket = ["a"];
assert.deepEqual(plain(sandbox.currentPrintOptions()).option_overrides, { a: "two" }, "Dropped publications must not remain in export overrides");
assert.deepEqual(plain(sandbox.currentPrintOptions()).question_breaks, []);
state.basket = [];
sandbox.prunePrintChoices();
state.basket = ["a", "b"];
assert.deepEqual(plain(sandbox.currentPrintOptions()).option_overrides, {}, "Clearing and re-adding questions must not revive old overrides");
assert.deepEqual(plain(sandbox.currentPrintOptions()).question_breaks, []);
delete state.basket;

// No empty answer document or half-loaded paper can be offered for download.
controls.printDocument.value = "answers";
sandbox.syncExportButtons();
assert(controls.printButton.disabled && controls.exportPdf.disabled && controls.exportWord.disabled && controls.exportSplit.disabled);
controls.printDocument.value = "combined";
sandbox.syncExportButtons();
assert.equal(controls.printButton.disabled, false);
assert.equal(controls.exportWord.disabled, false);
assert.equal(controls.exportSplit.disabled, true);
printState.availableAnswers = 1;
printState.tooWide = 1;
sandbox.syncExportButtons();
assert.equal(controls.printButton.disabled, true);
assert.equal(controls.exportPdf.disabled, true);
assert.equal(controls.exportWord.disabled, false, "Word editing remains available when a formula cannot fit A4");
assert.equal(controls.exportSplit.disabled, false);
printState.tooWide = 0; printState.layoutPending = true;
sandbox.syncExportButtons(); assert.equal(controls.exportPdf.disabled, true);
printState.layoutPending = false; printState.layoutError = "有图表高于A4";
sandbox.syncExportButtons(); assert.equal(controls.exportPdf.disabled, true);
printState.layoutError = "";
printState.missing = [{ id: "withdrawn" }];
sandbox.syncExportButtons();
assert(controls.printButton.disabled && controls.exportWord.disabled && controls.exportSplit.disabled);

vm.runInContext(source.slice(source.indexOf("  function syncIndividualControls("), source.indexOf("  async function refreshPrintPages(")), sandbox);
printState.items = [{ id: "second", content: { options: { A: "1" } } }];
printState.optionOverrides = {}; printState.questionBreaks = []; printState.firstQuestion = "first";
controls.printIndividualQuestion.value = "second";
printState.exporting = true;
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualOption.disabled, true, "Reloading the paper during export must not re-enable mutable controls");
assert.equal(controls.printIndividualBreak.disabled, true);
printState.exporting = false;
sandbox.syncIndividualControls(); assert.equal(controls.printIndividualOption.disabled, false); assert.equal(controls.printIndividualBreak.disabled, false);
printState.firstQuestion = "second"; sandbox.syncIndividualControls(); assert.equal(controls.printIndividualBreak.disabled, true, "The first question must not create an empty cover page");
printState.missing = []; printState.tooWide = 0; printState.exporting = true;
sandbox.syncExportButtons();
assert(controls.printButton.disabled && controls.exportWord.disabled && controls.exportSplit.disabled);

vm.runInContext(source.slice(source.indexOf("  async function waitForPrintAssets("), source.indexOf("  function setExportBusy(")), sandbox);
(async () => {
  let decoded = false;
  const image = { loading: "lazy", naturalWidth: 400, decode: async () => {
    assert.equal(image.loading, "eager", "Offscreen lazy figures must start loading before decode");
    decoded = true;
  } };
  ui.paper.querySelectorAll = () => [image];
  await sandbox.waitForPrintAssets();
  assert.equal(decoded, true);
  ui.paper.querySelectorAll = () => [{ naturalWidth: 0, decode: async () => {} }];
  await assert.rejects(sandbox.waitForPrintAssets(), /配图未能载入/);
  ui.paper.querySelectorAll = () => [{ naturalWidth: 0, decode: async () => { throw new Error("broken image"); } }];
  await assert.rejects(sandbox.waitForPrintAssets(), /配图未能载入/);
  console.log("library export UI invariants: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
