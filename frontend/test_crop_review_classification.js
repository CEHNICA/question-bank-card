"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const classification = source.slice(source.indexOf("  function approvalNeedsReview(q)"), source.indexOf("  function anyDialogOpen()"));
const counts = source.slice(source.indexOf("  function counts()"), source.indexOf("  function renderMeter(c)"));
const visibility = source.slice(source.indexOf("  function visible(q)"), source.indexOf("  function questionDeleteBlockReason(q)"));
const snapshot = source.slice(source.indexOf("  function cropSnapshot()"), source.indexOf("  function trackCropDraft()"));
const attention = source.slice(source.indexOf("  function syncCropDraftClassification()"), source.indexOf("  async function requestPageDialogClose()"));
const save = source.slice(source.indexOf("  async function savePageCrop("), source.indexOf('  $("pageDialogSave").addEventListener("click"'));
const clone = (value) => JSON.parse(JSON.stringify(value));

function harness(origin = "todo", seed = {}) {
  const q = { id: 7, number: 2, body_mode: "text", state: "yellow", stem: "Look at the four pictures.", approved: false,
    type_blocked: false, content_revision: 1, regions: [], figures: [], figure_review: { status: "blocked_missing" }, ...seed };
  const modal = { open: true, close() { this.open = false; } }, flags = [], requests = [];
  const context = {
    $: (id) => id === "pageDialog" ? modal : { value: "", focus() {} }, QBManualCrop: App,
    state: { paperId: "paper", paper: {}, filter: origin, questions: [q], cropDraftAttention: new Map(), rendered: new Map() },
    dialog: { mode: "figures", question: q, paperId: "paper", cropFilter: origin, boxes: [], ignoredCandidates: new Set(),
      session: 1, saving: false, sketch: null, pendingFigure: null },
    renderFilters: (value) => flags.push(clone(value)), renderMeter() {},
    showCropResult() {}, toast() {}, teach() {}, CROP_EDIT_KEY: "crop", editGuard: { release() {} },
    setCropSaving: (busy) => { context.dialog.saving = busy; },
    figuresFromBoxes: App.figuresFromBoxes, closeFigureSlotMenu() {}, refreshPaper() {},
    questionById: (id) => context.state.questions.find((question) => question.id === id),
    applyQuestion: (data) => { context.state.questions = [data.question]; },
    api: async (url, options) => {
      requests.push({ url, body: clone(options.body) });
      if (context.fail) throw Error("write failed");
      return { question: { ...context.state.questions[0], figures: options.body.figures, approved: false } };
    }
  };
  vm.runInNewContext(classification + counts + visibility + snapshot + attention + save, context);
  context.dialog.cropBaseline = JSON.stringify(context.cropSnapshot());
  context.freezeCropDraftClassification();
  return { context, q, modal, flags, requests };
}

(async () => {
  const unfinished = harness();
  assert.equal(unfinished.context.needsCheck(unfinished.q), true);
  unfinished.context.dialog.sketch = {};
  unfinished.context.updateCropDraftAttention();
  const freshlyRead = { ...unfinished.q, state: "green", figure_review: { status: "ready" }, stem: "Fresh backend reading" };
  // Background polling replaces the complete question object while the user
  // is still choosing corners. Draft classification must survive that read.
  unfinished.context.state.questions = [freshlyRead];
  assert.equal(unfinished.context.counts().todo, 1);
  assert.equal(unfinished.context.counts().green, 0);
  assert.equal(unfinished.context.visible(freshlyRead), true);
  assert.equal(unfinished.context.canApprove(freshlyRead), false);
  assert.equal(freshlyRead.approved, false);
  unfinished.context.dialog.sketch = null;
  unfinished.context.dialog.pendingFigure = { box: { page_idx: 0, bbox: [10, 20, 200, 220] } };
  unfinished.context.updateCropDraftAttention();
  assert.equal(unfinished.context.counts().todo, 1, "An outlined frame without an explicit option assignment remains重点核查");
  unfinished.context.dialog.pendingFigure = null;
  unfinished.context.dialog.boxes = [{ page_idx: 0, bbox: [10, 20, 200, 220], slot: "A" }];
  unfinished.context.updateCropDraftAttention();
  assert.equal(unfinished.context.counts().todo, 1, "Assigning a slot is still a local unsaved figure change");
  unfinished.context.fail = true;
  await unfinished.context.savePageCrop();
  assert.equal(unfinished.modal.open, true);
  assert.equal(unfinished.context.counts().todo, 1, "A failed save must retain the draft's review classification and original filter visibility");
  assert.equal(unfinished.context.visible(freshlyRead), true);
  assert.equal(unfinished.context.dialog.boxes.length, 1);
  unfinished.context.fail = false;
  await unfinished.context.savePageCrop();
  assert.equal(unfinished.modal.open, false);
  assert.equal(unfinished.context.state.cropDraftAttention.size, 0);
  assert.equal(unfinished.context.counts().todo, 0);
  assert.equal(unfinished.context.counts().green, 1, "Only a successful complete save returns the card to the backend's normal review class");
  assert.equal(unfinished.context.counts().approved, 0, "Saving a figure never approves or publishes the question");
  assert.equal(unfinished.requests.length, 2);

  const openedGreen = harness("green", { state: "green", figure_review: { status: "ready" } });
  const enteringCounts = clone(openedGreen.context.counts());
  assert.equal(openedGreen.context.counts().todo, 0, "Merely opening the figure editor is not a pending change");
  openedGreen.context.dialog.sketch = {};
  openedGreen.context.updateCropDraftAttention();
  assert.deepEqual(clone(openedGreen.context.counts()), enteringCounts, "Beginning a frame must keep every entering count unchanged");
  assert.equal(openedGreen.context.visible(openedGreen.q), true);
  assert.equal(openedGreen.context.canApprove(openedGreen.q), false, "A draft blocks approval without changing its displayed class");
  openedGreen.context.dialog.sketch = null;
  openedGreen.context.updateCropDraftAttention();
  assert.deepEqual(clone(openedGreen.context.counts()), enteringCounts, "Cancelling just an unfinished outline keeps the editor-session classification");
  openedGreen.context.dialog.boxes.push({ page_idx: 0, bbox: [10, 20, 200, 220], slot: "B" });
  openedGreen.context.updateCropDraftAttention();
  assert.deepEqual(clone(openedGreen.context.counts()), enteringCounts, "A fixed but unsaved option frame cannot move需要核查to重点核查");
  const changedGreen = { ...openedGreen.q, state: "red", figure_review: { status: "blocked_missing" } };
  openedGreen.context.state.questions = [changedGreen];
  for (const filter of ["all", "todo", "green", "approved", "ai"]) {
    openedGreen.context.state.filter = filter;
    const member = filter === "all" || filter === "green";
    assert.equal(openedGreen.context.visible(changedGreen), member, `Frozen visible membership is consistent with ${filter} count`);
  }
  openedGreen.context.state.filter = "green";
  assert.deepEqual(clone(openedGreen.context.counts()), enteringCounts, "Backend object replacement during editing cannot change counts");
  openedGreen.context.dialog.boxes = [];
  openedGreen.context.updateCropDraftAttention();
  assert.equal(openedGreen.context.cropDraftNeedsReview(changedGreen), false, "Undoing back to baseline clears dirty state only");
  assert.deepEqual(clone(openedGreen.context.counts()), enteringCounts, "Undo to baseline must retain session classification until closing");
  openedGreen.context.clearCropDraftAttention();
  assert.equal(openedGreen.context.counts().green, 0);
  assert.equal(openedGreen.context.counts().todo, 1);
  assert.equal(openedGreen.context.visible(changedGreen), false, "Explicit discard releases the freeze and returns real filter membership");

  const published = harness("approved", { state: "green", figure_review: { status: "ready" }, approved: true,
    publication: { up_to_date: true, id: "saved-version" } });
  const untouched = clone(published.q);
  const publishedCounts = clone(published.context.counts());
  published.context.dialog.sketch = {};
  published.context.updateCropDraftAttention();
  assert.deepEqual(clone(published.context.counts()), publishedCounts);
  assert.equal(published.context.visible(published.q), true);
  published.context.clearCropDraftAttention();
  assert.equal(published.context.counts().approved, 1);
  assert.deepEqual(published.q, untouched, "Local draft review markers never revoke real approval or change an existing publication");

  const fromAll = harness("all", { state: "green", figure_review: { status: "ready" } });
  const allCounts = clone(fromAll.context.counts());
  fromAll.context.dialog.pendingFigure = { box: { page_idx: 0, bbox: [10, 20, 200, 220] } };
  fromAll.context.updateCropDraftAttention();
  fromAll.context.state.questions = [{ ...fromAll.q, state: "yellow" }];
  assert.deepEqual(clone(fromAll.context.counts()), allCounts, "Opening from全部freezes the question's actual class rather than a filter key");
  fromAll.context.state.filter = "green";
  assert.equal(fromAll.context.visible(fromAll.context.state.questions[0]), true);
  fromAll.context.state.filter = "todo";
  assert.equal(fromAll.context.visible(fromAll.context.state.questions[0]), false);

  const openingOnly = harness();
  openingOnly.context.state.questions = [{ ...openingOnly.q, state: "green", figure_review: { status: "ready" } }];
  assert.equal(openingOnly.context.counts().todo, 1, "Freeze begins when the editor opens, before the first corner");
  assert.equal(openingOnly.context.counts().green, 0);
  assert.equal(openingOnly.context.visible(openingOnly.context.state.questions[0]), true);

  assert.match(source, /key: "todo", label: "重点核查"/);
  assert.match(source, /key: "green", label: "需要核查"/);
  assert.match(source, /key: "approved", label: "已通过"/);
  assert.doesNotMatch(source, /filterSelect|filterSelectionState/);
  console.log("Crop review: entering classification/counts frozen through sketch/assignment/dirty/undo/poll replacement; real classes released after save/discard; approval/publication immutable: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
