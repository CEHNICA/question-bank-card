"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const section = source.slice(source.indexOf("  let answerEditor = null;"), source.indexOf("  function printAnswerRow("));
assert(section.includes("scopeContext") && section.includes("deferAnswerRefresh"), "Test the actual parent callback and refresh scheduler");
const state = { draft: { id: "paper-a" }, draftSaving: false, catalog: new Map([["pub", { id: "pub" }]]), items: [{ id: "pub" }] };
const printState = { token: 1, exporting: false, items: [{ id: "pub" }], solutions: {}, solutionRecords: new Map() };
const frames = [], idle = [], closeListeners = [], counts = { list: 0, print: 0, dirty: 0 };
let editorOpen = false, callback, opened;
const dialog = { addEventListener: (event, fn) => { assert.equal(event, "close"); closeListeners.push(fn); } };
const context = { state, printState, ui: { sheet: { open: true } },
  $: id => { assert.equal(id, "answerEditorDialog"); return dialog; },
  requestAnimationFrame: fn => { frames.push(fn); },
  render: () => { counts.list++; }, renderPrint: () => { counts.print++; }, markDraftDirty: () => { counts.dirty++; },
  syncPrintAnswers() {}, syncExportButtons() {}, currentPrintOptions: () => ({ document: "combined" }), printAnswerContent: item => item.solution,
  toast() {}, node() {}, QB: {}, confirmDialog() {},
  window: { requestIdleCallback: fn => idle.push(fn), LibraryAnswerEditor: { create: options => { callback = options.onSaved; return {
    isOpen: () => editorOpen, open: (items, options) => { editorOpen = true; opened = options; }
  }; } } }
};
vm.createContext(context); vm.runInContext(section + "\nthis.openEditorForTest = openAnswerEditor;", context);
const frame = () => { const pending = frames.splice(0); for (const fn of pending) fn(); };
const flushIdle = () => { const pending = idle.splice(0); for (const fn of pending) fn(); };
const close = () => { editorOpen = false; for (const fn of closeListeners.splice(0)) fn(); };

context.openEditorForTest(printState.items, { scope: "paper" });
const originalContext = opened.scopeContext;
assert.equal(originalContext.token, 1); assert.equal(originalContext.draftId, "paper-a");
callback(printState.items[0], { id: "saved-a", answer: "A" }, { scope: "paper", sync: false, scopeContext: originalContext });
assert.equal(printState.solutions.pub, "saved-a"); assert.equal(counts.dirty, 1);
assert.equal(printState.layoutPending, true, "Export stays locked until deferred pages include the saved answer");
assert.equal(counts.print, 0); assert.equal(counts.list, 0); assert.equal(frames.length, 0, "No large hidden preview is rendered while editing");
close(); frame(); assert.equal(counts.print, 0, "The first frame lets the returned screen paint before pagination");
frame(); assert.equal(counts.print, 0, "Both paint frames finish before A4 work starts"); flushIdle(); assert.equal(counts.print, 1);

context.openEditorForTest(printState.items, { scope: "paper" });
const beforeSwitch = opened.scopeContext;
callback(printState.items[0], { id: "saved-before-switch" }, { scope: "paper", sync: false, scopeContext: beforeSwitch });
printState.token = 2; state.draft = { id: "paper-b" }; printState.items = [{ id: "pub" }]; printState.solutions = {}; printState.solutionRecords = new Map();
const dirtyBeforeLate = counts.dirty, printBeforeLate = counts.print;
callback({ id: "pub" }, { id: "late-old-paper" }, { scope: "paper", sync: false, scopeContext: originalContext });
assert.equal(printState.solutions.pub, undefined); assert.equal(printState.items[0].solution, undefined); assert.equal(counts.dirty, dirtyBeforeLate, "A previous paper's save cannot dirty or alter the new draft");
close(); frame(); frame(); flushIdle(); assert.equal(counts.print, printBeforeLate, "A queued refresh for an old paper is discarded after switching context");

context.openEditorForTest(printState.items, { scope: "paper" });
const newContext = opened.scopeContext;
callback({ id: "pub" }, { id: "old-sync" }, { scope: "paper", sync: true, scopeContext: originalContext });
assert.equal(state.catalog.get("pub").solution.id, "old-sync", "An explicit library sync can refresh the library even after switching papers");
assert.equal(printState.solutions.pub, undefined, "Library sync never selects an old paper's solution for a different draft");
callback({ id: "pub" }, { id: "new-paper" }, { scope: "paper", sync: false, scopeContext: newContext });
close(); frame();
editorOpen = true; frame(); flushIdle(); assert.equal(counts.print, printBeforeLate, "A fast reopen postpones the expensive refresh again");
close(); frame(); frame(); flushIdle();
assert.equal(counts.list, 1); assert.equal(counts.print, printBeforeLate + 1); assert.equal(printState.solutions.pub, "new-paper");
assert.equal(printState.items[0].solution.id, "new-paper");
// Multiple saves in the same editor create one close listener and one task.
context.openEditorForTest(printState.items, { scope: "paper" });
for (let index = 0; index < 8; index++) callback(printState.items[0], { id: `batch-${index}` }, { scope: "paper", sync: true, scopeContext: opened.scopeContext });
assert.equal(closeListeners.length, 1, "Repeated saves are batched instead of attaching a close listener for every save");
const beforeBatch = { ...counts }; close(); frame(); frame(); assert.equal(idle.length, 1); flushIdle();
assert.equal(counts.print, beforeBatch.print + 1); assert.equal(counts.list, beforeBatch.list + 1);

// A teacher sheet reuses its original typeset source; only the changed answer
// rows render again, then one pagination pass refreshes the pages.
let documentMode = "combined", pagination = 0; const replaced = [];
const removed = [];
const row = id => ({ dataset: { questionId: id }, classList: { contains: value => value === "print-answer-inline" }, replaceWith: value => replaced.push(value), remove: () => removed.push(id) });
const sourceRows = [row("pub"), row("unchanged")];
printState.layoutSource = { querySelectorAll: () => sourceRows };
context.currentPrintOptions = () => ({ document: documentMode });
context.groupedQuestionNumber = () => 1;
context.printAnswerRow = (number, item, inline) => ({ number, id: item.id, inline, answer: item.solution.id });
context.refreshPrintPages = source => { assert.equal(source, printState.layoutSource); pagination++; return Promise.resolve(); };
context.openEditorForTest(printState.items, { scope: "paper" });
callback(printState.items[0], { id: "changed-only" }, { scope: "paper", sync: false, scopeContext: opened.scopeContext });
const fullPrintBefore = counts.print; close(); frame(); frame(); flushIdle();
assert.equal(counts.print, fullPrintBefore, "A saved answer must not rebuild every original question and its KaTeX");
assert.deepEqual(replaced, [{ number: 1, id: "pub", inline: true, answer: "changed-only" }]); assert.equal(pagination, 1);
documentMode = "questions";
context.openEditorForTest(printState.items, { scope: "paper" });
callback(printState.items[0], { id: "answer-on-student" }, { scope: "paper", sync: false, scopeContext: opened.scopeContext });
close(); frame(); frame(); flushIdle(); assert.equal(pagination, 1, "A student sheet has no changed answer rows and needs no pagination");
assert.equal(counts.print, fullPrintBefore);

documentMode = "combined";
context.printAnswerContent = () => null; // Clear the explicit choice; the origin has no answer.
context.openEditorForTest(printState.items, { scope: "paper" });
callback(printState.items[0], { id: "back-to-no-origin-answer" }, { scope: "paper", sync: false, scopeContext: opened.scopeContext });
close(); frame(); frame(); flushIdle();
assert.deepEqual(removed, ["pub"], "Returning to an origin without an answer removes the inline row instead of a placeholder");
assert.equal(replaced.length, 1, "A removed solution does not build an empty answer row");
assert.equal(pagination, 2);
console.log("Answer parent refresh: old contexts isolated, one close/idle task, only changed answers rebuilt and student pages untouched: OK");
