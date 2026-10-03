"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const App = require("./app.js");
const { nextCropNumber } = App;

// Exercise the actual save/continue handler with a local transport and a small
// dialog shell. In particular, an empty next draft must never reach creation.
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const start = source.indexOf("  function continueManualCut(");
const end = source.indexOf('  $("pageDialogSave").addEventListener', start);
assert.ok(start > 0 && end > start);
const actions = source.slice(source.indexOf("  function configureCropActions("), source.indexOf('  $("pageDialog").addEventListener("cancel"'));
assert.match(actions, /\$\("pageDialogSave"\)\.hidden = \["view", "new"\]\.includes\(dialog\.mode\) \|\| dialog\.practiceRead;/);
assert.match(actions, /\$\("pageDialogSaveNext"\)\.hidden = dialog\.mode !== "new";/);
assert.match(actions, /\$\("pageDialogComplete"\)\.hidden = dialog\.mode !== "new";/);
assert.doesNotMatch(actions, /保存并关闭/);
const shortcutStart = source.indexOf('  $("pageDialog").addEventListener("keydown", (event) => {');
const shortcutEnd = source.indexOf('  $("manualProcessing").addEventListener', shortcutStart);
assert.ok(shortcutStart > 0 && shortcutEnd > shortcutStart);
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
function harness({ count = 25, number = count + 1, boxes = [], mode = "new", demo = false, teachingActive = false, failure = false, waitForWrite = null } = {}) {
  const requests = [], stages = [], messages = [], releases = [], toasts = [];
  const listeners = new Map();
  const nodes = {
    pageDialog: { open: true, close() { this.open = false; }, addEventListener(name, callback, capture) { listeners.set(name, { callback, capture }); } },
    numberInput: { value: String(number), focus() {} },
    cropTypeSelect: { value: "free_response" }, groupSelect: { value: "" },
    readTargetSelect: { value: "auto" }, pageDialogClose: {}
    , pageStage: { focus() {} }
  };
  const question = { id: 7, number: 1, regions: [{ page_idx: 0, bbox: [10, 10, 300, 100] }] };
  const state = { paperId: "qa-paper", paper: { demo },
    questions: Array.from({ length: count }, (_, i) => ({ id: i + 1, number: i + 1 })) };
  const dialog = { mode, boxes, paperId: state.paperId, session: 1, saving: false, cutQuestionIds: [],
    question: mode === "new" ? null : question, page: 2, zoom: 1.2 };
  const context = vm.createContext({
    $, state, dialog, teaching: { active: teachingActive, paper: state.paperId }, QBManualCrop: App, QBUpload: App, CROP_EDIT_KEY: "crop",
    document: { querySelector: () => null }, menuIsOpen: () => false,
    editGuard: { release(key) { releases.push(key); } },
    showCropResult(text) { messages.push(text); }, toast(text, kind) { toasts.push({ text, kind }); }, teach() {},
    setCropSaving(value) { dialog.saving = value; },
    readingOrder(value) { return value.map(({ page_idx, bbox }) => ({ page_idx, bbox: [...bbox] })); },
    closeFigureSlotMenu() {}, trackCropDraft() {}, renderPageTabs() {}, renderStage() {},
    cropSnapshot: () => ({ boxes: dialog.boxes }), clearCropDraftAttention() {},
    refreshPaper() {}, applyQuestion(data) {
      const idx = state.questions.findIndex((q) => q.id === data.question.id);
      if (idx < 0) state.questions.push(data.question); else state.questions[idx] = data.question;
    },
    async api(url, options) {
      requests.push({ url, ...options });
      if (waitForWrite) await waitForWrite;
      if (failure) throw Error("storage unavailable");
      return { question: { ...(mode === "new" ? { id: count + 1 } : question), ...options.body } };
    },
    async enterCutReadingStage(paperId, questionIds) {
      assert.equal(nodes.pageDialog.open, false, "The crop dialog closes before the reading stage opens");
      assert.equal(dialog.saving, false, "Finishing must release the save busy state");
      stages.push({ paperId, count: state.questions.length, questionIds: questionIds === null ? null : Array.from(questionIds) });
      return true;
    }
  });
  function $(id) { return nodes[id] || (nodes[id] = {}); }
  vm.runInContext(source.slice(start, end), context);
  vm.runInContext(source.slice(shortcutStart, shortcutEnd), context);
  return { context, nodes, state, dialog, requests, stages, messages, releases, toasts,
    save: (options) => context.savePageCrop(options),
    press(key, extra = {}, kind = "teachButton") {
      const event = { key, ctrlKey: true, target: { closest(selector) {
        if (["teachButton", "toolbarButton"].includes(kind) && selector.includes("button")) return this;
        if (kind === "canvas" && selector === "#pageStage") return this;
        if (kind === "input" && selector.includes("input")) return this;
        return null;
      } }, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra };
      assert.equal(listeners.get("keydown").capture, true);
      listeners.get("keydown").callback(event); return event;
    } };
}

(async () => {
  const empty = harness();
  await empty.save({ complete: true });
  await empty.save({ complete: true });
  assert.equal(empty.requests.length, 0, "Finishing empty question 26 must not send a create or OCR request");
  assert.equal(empty.state.questions.length, 25);
  assert.deepEqual(empty.stages, [{ paperId: "qa-paper", count: 25, questionIds: null }]);
  assert.equal(empty.messages.length, 0, "An empty next draft must not ask for another range");

  const last = harness({ count: 24, number: 25, boxes: [
    { page_idx: 1, bbox: [10, 20, 800, 250] }, { page_idx: 2, bbox: [10, 30, 800, 190] }
  ] });
  await last.save({ next: true });
  assert.equal(last.nodes.pageDialog.open, true);
  assert.equal(last.nodes.numberInput.value, 26);
  assert.equal(last.dialog.boxes.length, 0);
  assert.equal(last.dialog.page, 2); assert.equal(last.dialog.zoom, 1.2);
  assert.equal(last.stages.length, 0, "Continuous save never starts AI reading");
  assert.match(last.messages.at(-1), /第 25 题已保存/, "normal cutting keeps its saved-state feedback");
  assert.ok(last.toasts.some((notice) => notice.text.includes("第 25 题已保存")));
  await last.save({ complete: true });
  assert.equal(last.requests.length, 1, "Saving 25 and then finishing 26 creates exactly one real question");
  assert.equal(last.requests[0].body.processing_mode, "manual");
  assert.equal(last.requests[0].body.body_mode, "source_image");
  assert.deepEqual(last.requests[0].body.regions.map((r) => r.page_idx), [1, 2]);
  assert.deepEqual(last.stages, [{ paperId: "qa-paper", count: 25, questionIds: [25] }], "Completion reads only this batch's saved IDs");

  const finishWithBox = harness({ count: 24, number: 25, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }] });
  await finishWithBox.save({ complete: true });
  assert.equal(finishWithBox.requests.length, 1);
  assert.deepEqual(finishWithBox.stages, [{ paperId: "qa-paper", count: 25, questionIds: [25] }], "The final unsaved range is saved before entering reading");

  const failed = harness({ count: 24, number: 25, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }], failure: true });
  await failed.save({ complete: true });
  assert.equal(failed.nodes.pageDialog.open, true);
  assert.equal(failed.dialog.boxes.length, 1);
  assert.equal(failed.dialog.saving, false);
  assert.equal(failed.stages.length, 0, "A failed final save must not advance the workflow");
  assert.match(failed.messages.at(-1), /storage unavailable/);

  const guided = harness({ count: 0, number: 1, teachingActive: true, demo: true,
    boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }] });
  await guided.save({ next: true });
  assert.equal(guided.messages.at(-1), "", "the guide owns the successful-save message without a duplicate over the canvas");
  assert.equal(guided.toasts.length, 0, "a guided successful crop does not cover the question with a second toast");
  const guidedFailure = harness({ count: 0, number: 1, teachingActive: true, demo: true, failure: true,
    boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }] });
  await guidedFailure.save({ next: true });
  assert.match(guidedFailure.messages.at(-1), /未保存.*storage unavailable/);
  assert.ok(guidedFailure.toasts.some((notice) => notice.kind === "error"));

  let releaseWrite;
  const slow = harness({ boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }],
    waitForWrite: new Promise((resolve) => { releaseWrite = resolve; }) });
  const saving = slow.save({ next: true });
  await slow.save({ next: true });
  await slow.save({ complete: true });
  assert.equal(slow.requests.length, 1, "Repeated Enter or completion clicks share the existing save instead of duplicating questions");
  assert.equal(slow.stages.length, 0);
  releaseWrite(); await saving;
  assert.equal(slow.dialog.boxes.length, 0);
  assert.equal(slow.state.questions.length, 26);
  assert.equal(slow.dialog.saving, false);

  const adjusted = harness({ mode: "regions", boxes: [{ page_idx: 0, bbox: [10, 10, 500, 150] }] });
  await adjusted.save();
  assert.equal(adjusted.requests.length, 1);
  assert.match(adjusted.requests[0].url, /\/regions$/);
  assert.equal(adjusted.requests[0].body.processing_mode, "manual");
  assert.equal(adjusted.stages.length, 0);
  assert.equal(adjusted.nodes.pageDialog.open, false);

  const tutorial = harness({ count: 0, number: 1, demo: true, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }] });
  await tutorial.save({ next: true });
  assert.equal(tutorial.dialog.boxes.length, 0); assert.equal(tutorial.nodes.pageDialog.open, true);
  const tutorialFinish = tutorial.press("s", {}, "teachButton"); await flush();
  assert.equal(tutorialFinish.defaultPrevented, true); assert.equal(tutorialFinish.stopped, true);
  assert.equal(tutorial.requests.length, 1, "Tutorial button focus followed by Ctrl+S does not create an empty second question");
  assert.deepEqual(tutorial.stages, [{ paperId: "qa-paper", count: 1, questionIds: [1] }]);
  assert.equal(tutorial.nodes.pageDialog.open, false, "Ctrl+S completes the tutorial's cutting dialog without opening browser Save Page");

  const toolbar = harness({ count: 0, number: 1, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }] });
  assert.equal(toolbar.press("Enter", {}, "toolbarButton").defaultPrevented, true); await flush();
  assert.equal(toolbar.requests.length, 1); assert.equal(toolbar.nodes.pageDialog.open, false);
  assert.deepEqual(toolbar.stages, [{ paperId: "qa-paper", count: 1, questionIds: [1] }], "Toolbar Ctrl+Enter retains final-range saving before completion");

  const sketch = harness({ count: 0, number: 1 }); sketch.dialog.sketch = {};
  assert.equal(sketch.press("s").defaultPrevented, true); await flush();
  assert.equal(sketch.requests.length, 0); assert.equal(sketch.nodes.pageDialog.open, true); assert.equal(sketch.stages.length, 0);
  assert.match(sketch.messages.at(-1), /固定新框/, "Completion still requires the unfinished range to be fixed");

  let unlock;
  const keyboardSlow = harness({ count: 0, number: 1, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }],
    waitForWrite: new Promise(resolve => { unlock = resolve; }) });
  keyboardSlow.press("s", {}, "toolbarButton");
  keyboardSlow.press("s", {}, "teachButton"); keyboardSlow.press("Enter", {}, "toolbarButton");
  assert.equal(keyboardSlow.requests.length, 1); assert.equal(keyboardSlow.stages.length, 0);
  assert.equal(keyboardSlow.dialog.saving, true, "Toolbar/tutorial focus does not bypass the pending write lock");
  unlock(); await flush(); assert.equal(keyboardSlow.stages.length, 1);

  const keyboardFailed = harness({ count: 0, number: 1, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }], failure: true });
  keyboardFailed.press("s", {}, "teachButton"); await flush();
  assert.equal(keyboardFailed.nodes.pageDialog.open, true); assert.equal(keyboardFailed.dialog.boxes.length, 1);
  assert.equal(keyboardFailed.stages.length, 0); assert.equal(keyboardFailed.dialog.saving, false);
  assert.match(keyboardFailed.messages.at(-1), /storage unavailable/, "Shortcut completion preserves a failed final draft for retry");

  const input = harness({ count: 0, number: 1, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }] });
  assert.equal(input.press("s", {}, "input").defaultPrevented, true);
  input.press("s", { defaultPrevented: true }); input.press("Enter", { isComposing: true }); input.press("s", { repeat: true });
  assert.equal(input.requests.length, 0); assert.equal(input.stages.length, 0); assert.equal(input.nodes.pageDialog.open, true);
  console.log("Manual cutting completion, saved batch IDs, empty next draft and final-save failure: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
