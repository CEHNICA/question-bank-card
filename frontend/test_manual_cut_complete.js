"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { nextCropNumber } = require("./app.js");

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
assert.doesNotMatch(source, /保存并关闭|\$\("pageDialogSave"\)\.hidden = mode === "view"/);
assert.match(source, /\$\(dialog\.mode === "new" \? "pageDialogComplete" : "pageDialogSave"\)\.click\(\)/);
function harness({ count = 25, number = count + 1, boxes = [], mode = "new", failure = false } = {}) {
  const requests = [], stages = [], messages = [], releases = [];
  const nodes = {
    pageDialog: { open: true, close() { this.open = false; } },
    numberInput: { value: String(number), focus() {} },
    cropTypeSelect: { value: "free_response" }, groupSelect: { value: "" },
    readTargetSelect: { value: "auto" }, pageDialogClose: {}
  };
  const question = { id: 7, number: 1, regions: [{ page_idx: 0, bbox: [10, 10, 300, 100] }] };
  const state = { paperId: "qa-paper", paper: { demo: false },
    questions: Array.from({ length: count }, (_, i) => ({ id: i + 1, number: i + 1 })) };
  const dialog = { mode, boxes, paperId: state.paperId, session: 1, saving: false,
    question: mode === "new" ? null : question, page: 2, zoom: 1.2 };
  const context = vm.createContext({
    $, state, dialog, QBManualCrop: { nextCropNumber }, CROP_EDIT_KEY: "crop",
    editGuard: { release(key) { releases.push(key); } },
    showCropResult(text) { messages.push(text); }, toast() {},
    setCropSaving(value) { dialog.saving = value; },
    readingOrder(value) { return value.map(({ page_idx, bbox }) => ({ page_idx, bbox: [...bbox] })); },
    closeFigureSlotMenu() {}, trackCropDraft() {}, renderPageTabs() {}, renderStage() {},
    refreshPaper() {}, applyQuestion(data) {
      const idx = state.questions.findIndex((q) => q.id === data.question.id);
      if (idx < 0) state.questions.push(data.question); else state.questions[idx] = data.question;
    },
    async api(url, options) {
      requests.push({ url, ...options });
      if (failure) throw Error("storage unavailable");
      return { question: { ...(mode === "new" ? { id: count + 1 } : question), ...options.body } };
    },
    async enterCutReadingStage(paperId) {
      assert.equal(nodes.pageDialog.open, false, "The crop dialog closes before the reading stage opens");
      assert.equal(dialog.saving, false, "Finishing must release the save busy state");
      stages.push({ paperId, count: state.questions.length });
      return true;
    }
  });
  function $(id) { return nodes[id] || (nodes[id] = {}); }
  vm.runInContext(source.slice(start, end), context);
  return { context, nodes, state, dialog, requests, stages, messages, releases,
    save: (options) => context.savePageCrop(options) };
}

(async () => {
  const empty = harness();
  await empty.save({ complete: true });
  await empty.save({ complete: true });
  assert.equal(empty.requests.length, 0, "Finishing empty question 26 must not send a create or OCR request");
  assert.equal(empty.state.questions.length, 25);
  assert.deepEqual(empty.stages, [{ paperId: "qa-paper", count: 25 }]);
  assert.equal(empty.messages.length, 0, "An empty next draft must not ask for another range");

  const last = harness({ count: 24, number: 25, boxes: [
    { page_idx: 1, bbox: [10, 20, 800, 250] }, { page_idx: 2, bbox: [10, 30, 800, 190] }
  ] });
  await last.save({ next: true });
  assert.equal(last.nodes.pageDialog.open, true);
  assert.equal(last.nodes.numberInput.value, 26);
  assert.equal(last.dialog.boxes.length, 0);
  assert.equal(last.dialog.page, 2); assert.equal(last.dialog.zoom, 1.2);
  await last.save({ complete: true });
  assert.equal(last.requests.length, 1, "Saving 25 and then finishing 26 creates exactly one real question");
  assert.equal(last.requests[0].body.processing_mode, "manual");
  assert.equal(last.requests[0].body.body_mode, "source_image");
  assert.deepEqual(last.requests[0].body.regions.map((r) => r.page_idx), [1, 2]);
  assert.deepEqual(last.stages, [{ paperId: "qa-paper", count: 25 }]);

  const finishWithBox = harness({ count: 24, number: 25, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }] });
  await finishWithBox.save({ complete: true });
  assert.equal(finishWithBox.requests.length, 1);
  assert.deepEqual(finishWithBox.stages, [{ paperId: "qa-paper", count: 25 }], "The final unsaved range is saved before entering reading");

  const failed = harness({ count: 24, number: 25, boxes: [{ page_idx: 0, bbox: [10, 10, 900, 200] }], failure: true });
  await failed.save({ complete: true });
  assert.equal(failed.nodes.pageDialog.open, true);
  assert.equal(failed.dialog.boxes.length, 1);
  assert.equal(failed.dialog.saving, false);
  assert.equal(failed.stages.length, 0, "A failed final save must not advance the workflow");
  assert.match(failed.messages.at(-1), /storage unavailable/);

  const adjusted = harness({ mode: "regions", boxes: [{ page_idx: 0, bbox: [10, 10, 500, 150] }] });
  await adjusted.save();
  assert.equal(adjusted.requests.length, 1);
  assert.match(adjusted.requests[0].url, /\/regions$/);
  assert.equal(adjusted.requests[0].body.processing_mode, "manual");
  assert.equal(adjusted.stages.length, 0);
  assert.equal(adjusted.nodes.pageDialog.open, false);
  console.log("Manual cutting completion, empty next draft, final-save failure and separate OCR stage: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
