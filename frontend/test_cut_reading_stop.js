"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Cut = require("./app.js");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const handler = js.slice(js.indexOf("  async function stopCutReading("), js.indexOf("  function setFilter("));
const saved = [{ id: 1, body_mode: "source_image", content_revision: 8, ocr_pending: true,
  regions: [{ page_idx: 1, bbox: [10, 20, 900, 500] }], approved: false },
{ id: 2, body_mode: "source_image", content_revision: 4, ocr_suggestion: { revision: 4, stem: "x + 1 = 2" },
  regions: [{ page_idx: 0, bbox: [10, 20, 900, 500] }], approved: true }];

function scenario() {
  const questions = JSON.parse(JSON.stringify(saved));
  const requests = [];
  const messages = [];
  const context = { state: { paperId: "cut-test", paper: { status: "ready" }, questions },
    cutReadingStops: new Set(), cutReadingRequests: new Set(), questionReadingRequests: new Set(),
    cutReadingErrors: new Map(), cutReadingStopErrors: new Map(), QBCutReading: Cut,
    paperReadSubmissionPending: () => context.cutReadingStops.has(context.state.paperId) || context.cutReadingRequests.has(context.state.paperId),
    QBRegionWait: { boundedRequest: (task) => task(undefined) }, renderReadingControls() {},
    refreshPaper: async () => { questions[0].ocr_pending = false; questions[0].content_revision += 1; },
    api: async (url, options) => { requests.push({ url, method: options.method, body: options.body }); return { stopped: 1, message: "Local reading stopped" }; },
    toast: (message, kind) => messages.push({ message, kind }) };
  return { context, questions, requests, messages };
}

(async () => {
  const success = scenario();
  success.context.cutReadingErrors.set("cut-test", "Old reading submission error");
  await vm.runInNewContext(handler + "\nstopCutReading();", success.context);
  assert.equal(success.requests.length, 1);
  assert.equal(success.requests[0].url, "/api/papers/cut-test/stop-cut-reading");
  assert.equal(success.requests[0].method, "POST");
  assert.deepEqual(JSON.parse(JSON.stringify(success.requests[0].body)), {});
  assert.deepEqual(success.questions.map((q) => q.regions), saved.map((q) => q.regions));
  assert.deepEqual(success.questions[1], saved[1], "Stopping keeps successful suggestions and existing approval");
  assert.equal(success.questions[0].ocr_pending, false);
  assert.equal(success.context.cutReadingErrors.has("cut-test"), false);
  assert.equal(success.context.cutReadingStops.size, 0);
  assert.deepEqual(success.messages, [{ message: "Local reading stopped", kind: "success" }]);
  await vm.runInNewContext(handler + "\nstopCutReading();", success.context);
  assert.equal(success.requests.length, 1, "No pending task means no repeated stop or automatic reread request");

  const failure = scenario();
  failure.context.api = async () => { throw new Error("Offline"); };
  const before = JSON.stringify(failure.questions);
  await vm.runInNewContext(handler + "\nstopCutReading();", failure.context);
  assert.equal(JSON.stringify(failure.questions), before, "A failed stop cannot pretend that reading was stopped");
  assert.match(failure.context.cutReadingStopErrors.get("cut-test"), /未能停止识读：Offline/);
  assert.equal(failure.context.cutReadingStops.size, 0, "The button remains available for a retry after failure");
  assert.equal(failure.messages[0].kind, "error");

  const busy = scenario();
  busy.context.cutReadingRequests.add("cut-test");
  await vm.runInNewContext(handler + "\nstopCutReading();", busy.context);
  assert.equal(busy.requests.length, 0, "Stop and submit cannot race within this page");
  assert.match(js, /button\(cutReadingStops\.has\(paper\.id\) \? "正在停止识读……" : "停止识读"/);

  // A poll that started before stopping cannot revive the old pending display
  // after the newer refresh has already confirmed the stop.
  const refresh = js.slice(js.indexOf("  async function refreshPaper("), js.indexOf("  // AI 助手通过"));
  const waits = [];
  const refreshContext = { state: { paperId: "cut-test", papers: [], questions: [], selected: new Set(), selectionAnchor: null },
    paperRefreshToken: 0, ACTIVE_STATUS: new Set(), clearTimeout() {}, setTimeout() { throw new Error("Unexpected poll"); },
    $: (id) => ({ open: false }), normalizeRegionRead: (q) => q,
    renderPaper() {}, renderPaperList() {}, renderViewer() {}, regionReadPending: () => false, toast() {},
    api: () => new Promise((resolve) => waits.push(resolve)) };
  const oldPoll = vm.runInNewContext(refresh + "\nrefreshPaper();", refreshContext);
  const newPoll = vm.runInNewContext(refresh + "\nrefreshPaper();", refreshContext);
  waits[1]({ paper: { id: "cut-test", status: "ready" }, questions: [{ id: 1, content_revision: 9, ocr_pending: false }] });
  assert.equal(await newPoll, true);
  waits[0]({ paper: { id: "cut-test", status: "ready" }, questions: [{ id: 1, content_revision: 8, ocr_pending: true }] });
  assert.equal(await oldPoll, false);
  assert.equal(refreshContext.state.questions[0].ocr_pending, false);
  assert.equal(refreshContext.state.questions[0].content_revision, 9);
  console.log("Cut reading explicit stop, retained originals/suggestions, failure retry and submission lock: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
