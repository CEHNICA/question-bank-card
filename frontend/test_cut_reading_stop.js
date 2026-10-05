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
    // 停止失败时错误改由这块面板自己的红条常驻显示，toast 不再重复一遍。
    renderCutReadingStage() { context.stagesRendered = (context.stagesRendered || 0) + 1; },
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

  const manualText = scenario();
  Object.assign(manualText.questions[0], { body_mode: "text", processing_mode: "manual", stem: "Retained manual text",
    options: { A: "1", B: "2" }, figures: [{ page_idx: 1, bbox: [20, 30, 400, 250], slot: "stem" }] });
  const textEvidence = JSON.stringify({ stem: manualText.questions[0].stem, options: manualText.questions[0].options,
    figures: manualText.questions[0].figures, regions: manualText.questions[0].regions });
  await vm.runInNewContext(handler + "\nstopCutReading();", manualText.context);
  assert.equal(manualText.requests.length, 1, "A manual question remains stoppable after its first reading turned it into text");
  assert.equal(manualText.questions[0].ocr_pending, false);
  assert.equal(manualText.questions[0].body_mode, "text");
  assert.equal(JSON.stringify({ stem: manualText.questions[0].stem, options: manualText.questions[0].options,
    figures: manualText.questions[0].figures, regions: manualText.questions[0].regions }), textEvidence);

  // A manually cut supplement can belong to an otherwise cloud-parsed paper.
  // Its normal text rereading still needs a visible stop action.
  const stage = js.slice(js.indexOf("  function renderCutReadingStage("), js.indexOf("  function openManualCut("));
  const stageNodes = new Map(), buttons = [];
  const node = () => ({ hidden: false, dataset: {}, children: [], append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; }, setAttribute() {} });
  const stageContext = { state: { paper: { id: "manual-text", parse_mode: "mineru", status: "ready" },
    questions: [{ ...manualText.questions[0], ocr_pending: true }] }, QBCutReading: Cut,
    cutReadingErrors: new Map(), cutReadingStopErrors: new Map(), directImageReview: new Set(),
    cutReadingRequests: new Set(), cutReadingStops: new Set(), paperReadSubmissionPending: () => false,
    manualSwitches: new Set(), aiCutContinuations: new Set(),
    $: (id) => { if (!stageNodes.has(id)) stageNodes.set(id, node()); return stageNodes.get(id); },
    document: { createTextNode: (text) => text }, el: () => node(),
    button: (label) => { buttons.push(label); return node(); }, stopCutReading() {}, openManualCut() {}, focusCutReview() {}, readCutQuestions() {} };
  vm.runInNewContext(stage + "\nrenderCutReadingStage();", stageContext);
  assert.equal(stageNodes.get("cutReadingStage").hidden, false);
  assert.match(stageNodes.get("cutReadingTitle").textContent, /1 道题正在 AI 识读/);
  assert.ok(buttons.includes("停止识读"));
  assert.ok(!buttons.some((label) => /识读未完成题目/.test(label)), "Text rereading must not produce a duplicate batch reading action");

  const failure = scenario();
  failure.context.api = async () => { throw new Error("Offline"); };
  const before = JSON.stringify(failure.questions);
  await vm.runInNewContext(handler + "\nstopCutReading();", failure.context);
  assert.equal(JSON.stringify(failure.questions), before, "A failed stop cannot pretend that reading was stopped");
  assert.match(failure.context.cutReadingStopErrors.get("cut-test"), /未能停止识读：Offline/);
  assert.equal(failure.context.cutReadingStops.size, 0, "The button remains available for a retry after failure");
  // 1.12.7：失败提示只留这块面板自己的红条一处，不再叠一个飘过来的 toast
  // —— toast 固定在右下角、不挡鼠标之前，正好压在题卡的「改字 / 调整范围」上。
  assert.equal(failure.messages.length, 0, "The same sentence must not appear twice");
  assert.equal(failure.context.stagesRendered, 1, "The red bar is re-rendered so the sentence is actually visible");

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
    renderPaper() {}, renderPaperList() {}, renderViewer() {}, readNewlyCutUpload() {}, regionReadPending: () => false, toast() {},
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
