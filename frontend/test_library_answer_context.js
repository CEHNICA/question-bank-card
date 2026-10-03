"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const section = source.slice(source.indexOf("  let answerEditor = null;"), source.indexOf("  function printAnswerRow("));
assert(section.includes("scopeContext") && section.includes("deferAnswerRefresh"), "Test the actual parent callback and refresh scheduler");
const state = { draft: { id: "paper-a" }, draftSaving: false, catalog: new Map([["pub", { id: "pub" }]]), items: [{ id: "pub" }] };
const printState = { token: 1, exporting: false, items: [{ id: "pub" }], solutions: {}, solutionRecords: new Map() };
const frames = [], closeListeners = [], counts = { list: 0, print: 0, dirty: 0 };
let editorOpen = false, callback, opened;
const dialog = { addEventListener: (event, fn) => { assert.equal(event, "close"); closeListeners.push(fn); } };
const context = { state, printState, ui: { sheet: { open: true } },
  $: id => { assert.equal(id, "answerEditorDialog"); return dialog; },
  requestAnimationFrame: fn => { frames.push(fn); },
  render: () => { counts.list++; }, renderPrint: () => { counts.print++; }, markDraftDirty: () => { counts.dirty++; },
  toast() {}, node() {}, QB: {}, confirmDialog() {},
  window: { LibraryAnswerEditor: { create: options => { callback = options.onSaved; return {
    isOpen: () => editorOpen, open: (items, options) => { editorOpen = true; opened = options; }
  }; } } }
};
vm.createContext(context); vm.runInContext(section + "\nthis.openEditorForTest = openAnswerEditor;", context);
const frame = () => { const pending = frames.splice(0); for (const fn of pending) fn(); };
const close = () => { editorOpen = false; for (const fn of closeListeners.splice(0)) fn(); };

context.openEditorForTest(printState.items, { scope: "paper" });
const originalContext = opened.scopeContext;
assert.equal(originalContext.token, 1); assert.equal(originalContext.draftId, "paper-a");
callback(printState.items[0], { id: "saved-a", answer: "A" }, { scope: "paper", sync: false, scopeContext: originalContext });
assert.equal(printState.solutions.pub, "saved-a"); assert.equal(counts.dirty, 1);
assert.equal(counts.print, 0); assert.equal(counts.list, 0); assert.equal(frames.length, 0, "No large hidden preview is rendered while editing");
close(); frame(); assert.equal(counts.print, 0, "The first frame lets the returned screen paint before pagination");
frame(); assert.equal(counts.print, 1, "The second frame renders the current paper after the editor closes");

context.openEditorForTest(printState.items, { scope: "paper" });
const beforeSwitch = opened.scopeContext;
callback(printState.items[0], { id: "saved-before-switch" }, { scope: "paper", sync: false, scopeContext: beforeSwitch });
printState.token = 2; state.draft = { id: "paper-b" }; printState.items = [{ id: "pub" }]; printState.solutions = {}; printState.solutionRecords = new Map();
const dirtyBeforeLate = counts.dirty, printBeforeLate = counts.print;
callback({ id: "pub" }, { id: "late-old-paper" }, { scope: "paper", sync: false, scopeContext: originalContext });
assert.equal(printState.solutions.pub, undefined); assert.equal(printState.items[0].solution, undefined); assert.equal(counts.dirty, dirtyBeforeLate, "A previous paper's save cannot dirty or alter the new draft");
close(); frame(); frame(); assert.equal(counts.print, printBeforeLate, "A queued refresh for an old paper is discarded after switching context");

context.openEditorForTest(printState.items, { scope: "paper" });
const newContext = opened.scopeContext;
callback({ id: "pub" }, { id: "old-sync" }, { scope: "paper", sync: true, scopeContext: originalContext });
assert.equal(state.catalog.get("pub").solution.id, "old-sync", "An explicit library sync can refresh the library even after switching papers");
assert.equal(printState.solutions.pub, undefined, "Library sync never selects an old paper's solution for a different draft");
callback({ id: "pub" }, { id: "new-paper" }, { scope: "paper", sync: false, scopeContext: newContext });
close(); frame();
editorOpen = true; frame(); assert.equal(counts.print, printBeforeLate, "A fast reopen postpones the expensive refresh again");
close(); frame(); frame();
assert.equal(counts.list, 1); assert.equal(counts.print, printBeforeLate + 1); assert.equal(printState.solutions.pub, "new-paper");
assert.equal(printState.items[0].solution.id, "new-paper");
console.log("Answer parent context: old paper saves stay isolated and pagination waits until close plus two frames: OK");
