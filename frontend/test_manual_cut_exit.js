"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const between = (start, end) => {
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `Missing production block: ${start}`);
  return source.slice(first, last);
};
const snapshot = between("  function cropSnapshot()", "  function syncCropDraftClassification()");
const closing = between("  function releasePageDialog()", "  function showCropResult(");
const opening = between("  function openPageDialog(", '  $("groupSelect").addEventListener');
const zoom = between("  function requestPageZoom(", "  function zoomPageBy(");
const switchCode = between("  const manualSwitches = new Set();", "  const resegmentPreview =");
const closeEvent = between('  $("pageDialog").addEventListener("close"', "  function renderStage()");
const imageLoading = between("  function renderStage()", "    const q = dialog.question;")
  + "    stage.append(surface); globalThis.lastImage = image; globalThis.lastSurface = surface;\n  }\n";

class Node {
  constructor() {
    this.children = []; this.events = {}; this.attrs = {}; this.open = false; this.value = "";
    this.style = {}; this.dataset = {}; this.isConnected = true;
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children.forEach(item => { if (item && typeof item === "object") item.isConnected = false; }); this.children = items; }
  querySelectorAll() { return this.children.flatMap(item => item?.tag === "img" ? [item] : item?.querySelectorAll?.("img") || []); }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  emit(name) { (this.events[name] || []).forEach(callback => callback()); }
  setAttribute(name, value) { this.attrs[name] = value; }
  removeAttribute(name) { delete this.attrs[name]; if (name === "src") this.srcRemoved = true; }
  scrollTo() {}
  focus() {}
}

function harness(options = {}) {
  const nodes = new Map(), closeEvents = [], frames = new Map(), deferred = [], calls = [], notices = [];
  let frameId = 0, stageRenders = 0, entryRenders = 0, confirms = 0, zooms = 0;
  const $ = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  $("pageDialog").showModal = function () { this.open = true; };
  $("pageDialog").close = function () { this.open = false; closeEvents.push(() => this.emit("close")); };
  const pages = [{ page_idx: 0, width: 600, height: 900 }];
  const state = { paperId: "paper", paper: { id: "paper", status: "ready", parse_mode: "manual", pages }, questions: [] };
  const dialog = { mode: null, question: null, boxes: [], page: 0, session: 0, active: false, reopenIntent: null,
    saving: false, closing: false, imageReady: false, lastPage: null, zoomFrame: 0, ignoredCandidates: new Set() };
  const context = {
    $, state, dialog, pageOpenIntent: 0, CROP_EDIT_KEY: "original-crop", AbortController,
    QBManualCrop: App, QBProgress: App, QBRegionWait: App, editGuard: App.createEditGuard(),
    newUploadReadContinuations: new Set(), TYPE_NAMES: { unknown: "题型未定" },
    window: { innerWidth: 1100 }, lens: new Node(), document: { createTextNode: text => text },
    saveReviewState() {}, readReviewState: () => null,
    el: tag => { const node = new Node(); node.tag = tag; return node; },
    cancelFigureSketch() { dialog.sketch?.cancel(); },
    freezeCropDraftClassification() {}, updateCropDraftAttention() {}, clearCropDraftAttention: () => false,
    clearPagePanKey() {}, closeFigureSlotMenu() { dialog.pendingFigure = null; },
    setCropSaving(value) { dialog.saving = value; },
    hasRegionReadSubmission: () => false, cancelRegionReadSubmission() {},
    showCropResult() {}, showCropGuide() {}, configureCropActions() {}, updatePageCanvasHint() {},
    renderPageTabs() {}, renderRegionPieces() {}, renderCards() { throw Error("Clean closing must not rebuild cards"); },
    renderStage() { stageRenders++; }, renderSettingsTask() {}, renderCutReadingStage() { entryRenders++; },
    applyPageZoom() { zooms++; }, readTargetGuess: () => "stem", pageInfo: () => pages[0], previewUrl: () => "/local-page.png",
    toast: message => notices.push(message),
    confirmDialog: async () => { confirms++; return options.confirm !== false; },
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); }, setTimeout(callback) { deferred.push(callback); return deferred.length; },
    updatePaperFromResponse(paper) { state.paper = paper; }, refreshPaper: async () => options.refresh ? options.refresh(context) : true,
    api: (url, args) => { calls.push({ url, args }); return options.api ? options.api(calls.length, context) : Promise.resolve({ paper: state.paper }); }
  };
  vm.runInNewContext(snapshot + closing + opening + zoom + switchCode + closeEvent
    + "\nglobalThis.manualBusy = manualSwitches; globalThis.aiBusy = aiCutContinuations;", context);
  return { context, $, nodes, closeEvents, frames, deferred, calls, notices,
    stats: () => ({ stageRenders, entryRenders, confirms, zooms }),
    flushClose() { while (closeEvents.length) closeEvents.shift()(); } };
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

(async () => {
  const clean = harness(); clean.context.openPageDialog("new");
  const image = new Node(); image.tag = "img";
  clean.$("pageStage").append(image); clean.$("pageCropPreviewBody").append(image);
  const session = clean.context.dialog.session;
  const closed = clean.context.requestPageDialogClose();
  assert.equal(clean.$("pageDialog").open, false, "An untouched manual dialog closes before waiting for a promise or a network response");
  assert.equal(clean.context.dialog.session, session + 1);
  assert.equal(image.srcRemoved, true, "Large page and stitched image requests are detached on exit");
  assert.equal(clean.$("pageStage").children.length, 0);
  assert.equal(clean.$("pageCropPreviewBody").children.length, 0);
  assert.equal(clean.stats().confirms, 0);
  assert.equal(await closed, true); clean.flushClose();
  assert.equal(clean.context.dialog.session, session + 1, "A queued native close event does not clean the same editor twice");
  assert.equal(clean.calls.length, 0, "Opening and leaving ready manual pages starts no processing/OCR task");

  const dirty = harness({ confirm: false }); dirty.context.openPageDialog("regions", { id: 1, number: 1, regions: [] });
  dirty.context.dialog.boxes.push({ page_idx: 0, bbox: [10, 10, 100, 100] });
  assert.equal(await dirty.context.requestPageDialogClose(), false);
  assert.equal(dirty.$("pageDialog").open, true); assert.equal(dirty.context.dialog.boxes.length, 1);
  dirty.context.dialog.saving = true;
  assert.equal(await dirty.context.requestPageDialogClose(), false);
  assert.equal(dirty.$("pageDialog").open, true, "Saving and unsaved ranges keep their existing exit protection");
  assert.equal(dirty.stats().confirms, 1);
  dirty.context.dialog.saving = false; dirty.context.confirmDialog = async () => true;
  assert.equal(await dirty.context.requestPageDialogClose(), true);
  assert.equal(dirty.context.editGuard.hasPendingWork(), false);

  let firstResolve, secondResolve;
  const preparing = harness({ api: number => new Promise(resolve => {
    if (number === 1) firstResolve = resolve; else secondResolve = resolve;
  }) });
  preparing.context.state.paper.status = "failed";
  preparing.context.openPageDialog("view");
  const first = preparing.context.switchToManual(); await settle();
  assert.equal(preparing.context.manualBusy.has("paper"), true);
  assert.equal(preparing.$("emptyManualCut").disabled, true);
  await preparing.context.requestPageDialogClose();
  assert.equal(preparing.calls[0].args.signal.aborted, true);
  assert.equal(preparing.context.manualBusy.size, 0);
  assert.equal(preparing.$("emptyManualCut").disabled, false, "All manual entries release immediately when the pending view is left");
  const second = preparing.context.switchToManual(); await settle();
  assert.equal(await first, false);
  assert.equal(preparing.context.manualBusy.has("paper"), true, "The older aborted finally cannot clear a newer request's busy state");
  firstResolve({ paper: { ...preparing.context.state.paper, status: "ready" } }); await settle();
  assert.equal(preparing.$("pageDialog").open, false, "A late result ignored by an aborted client cannot reopen the old canvas");
  secondResolve({ paper: { ...preparing.context.state.paper, status: "ready" } });
  assert.equal(await second, true);
  assert.equal(preparing.$("pageDialog").open, true);
  assert.equal(preparing.context.manualBusy.size, 0);
  assert.equal(preparing.$("emptyManualCut").disabled, false);
  assert.ok(preparing.stats().entryRenders >= 4, "Both start and every terminal path update the cutting panel, without depending on polling");

  const reopen = harness(); reopen.context.openPageDialog("view");
  reopen.context.openPageDialog("new");
  reopen.context.cancelPendingPageOpening();
  await settle();
  assert.equal(reopen.$("pageDialog").open, false, "A later user exit supersedes a queued close/reopen continuation");
  reopen.context.openPageDialog("new"); const reopenedSession = reopen.context.dialog.session;
  reopen.flushClose();
  assert.equal(reopen.$("pageDialog").open, true);
  assert.equal(reopen.context.dialog.session, reopenedSession, "The previous close event cannot detach the newly opened editor");
  reopen.context.requestPageZoom("fit");
  const callbacks = [...reopen.frames.values()];
  await reopen.context.requestPageDialogClose();
  reopen.context.openPageDialog("new");
  callbacks.forEach(callback => callback());
  assert.equal(reopen.stats().zooms, 0, "Obsolete animation frames never move or zoom a reopened editor");

  const loads = harness(); loads.context.openPageDialog("new");
  vm.runInNewContext(imageLoading + "\nrenderStage();", loads.context);
  const oldImage = loads.context.lastImage, oldSurface = loads.context.lastSurface;
  await loads.context.requestPageDialogClose(); loads.context.openPageDialog("new");
  // Keep the old surface artificially connected to verify the session guard
  // independently of normal DOM detachment.
  oldSurface.isConnected = true; oldImage.emit("load"); oldImage.emit("error");
  assert.equal(loads.context.dialog.imageReady, false); assert.equal(loads.stats().zooms, 0);

  const mutual = harness(); mutual.context.aiBusy.add("paper");
  assert.equal(await mutual.context.switchToManual(), false); assert.equal(mutual.calls.length, 0);
  console.log("Manual cutting exit: immediate clean close, dirty/save protection, image release, aborted late callbacks, old/new request deduplication, busy reset, reopen/zoom/session safety: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
