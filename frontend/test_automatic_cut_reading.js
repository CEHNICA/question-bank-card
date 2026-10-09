"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const Cut = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const handlers = source.slice(source.indexOf("  async function enterCutReadingStage("), source.indexOf("  function setFilter("));
const upload = source.slice(source.indexOf("  async function sendUpload("), source.indexOf("  async function handleFiles("));
const imageQuestion = (id, extra = {}) => ({ id, content_revision: 5, body_mode: "source_image",
  regions: [{ page_idx: 0, bbox: [10, 20, 800, 250] }], ...extra });

function harness({ reader = "Selected vision service", failure = false, questions } = {}) {
  const requests = [], messages = []; const nodes = { pageDialog: { open: true, close() { this.open = false; } } };
  const context = {
    state: { paperId: "new-paper", paper: { id: "new-paper", status: "ready", parse_mode: "native", processing_plan: { revision: 3 } },
      status: { reader, assistant_mode: false }, questions: questions || [imageQuestion(1), imageQuestion(2), imageQuestion(3, { approved: true })] },
    newUploadReadContinuations: new Set(), cutReadingRequests: new Set(), cutReadingStops: new Set(),
    cutReadingErrors: new Map(), cutReadingStopErrors: new Map(), directImageReview: new Set(), QBCutReading: Cut,
    QBRegionWait: { boundedRequest: (task) => task(undefined) },
    paperReadSubmissionPending: () => context.cutReadingRequests.has(context.state.paperId) || context.cutReadingStops.has(context.state.paperId),
    renderReadingControls() {}, renderCutReadingStage() {}, refreshPaper: async () => true,
    toast: (message) => messages.push(message),
    $: (id) => nodes[id] || (nodes[id] = { scrollIntoView() {}, focus() {} }),
    api: async (url, options) => {
      requests.push({ url, method: options.method, body: options.body ? JSON.parse(JSON.stringify(options.body)) : undefined });
      if (url === "/api/papers") return { paper: { id: "uploaded-paper" }, duplicate: Boolean(context.duplicate) };
      assert.equal(nodes.pageDialog.open, false, "Finishing closes the crop dialog before queuing OCR");
      if (failure) throw Error("AI 识读需要看图读题服务，请在设置 → 读题服务配置密钥");
      return { queued: options.body.question_ids.length };
    }
  };
  vm.runInNewContext(handlers + upload, context);
  return { context, nodes, requests, messages };
}

(async () => {
  const complete = harness();
  await complete.context.enterCutReadingStage("new-paper", [2]);
  assert.equal(complete.nodes.pageDialog.open, false);
  assert.deepEqual(complete.requests, [], "Finishing manual cutting never submits AI reading");
  assert.equal(complete.context.cutReadingRequests.size, 0);
  assert.equal(complete.context.newUploadReadContinuations.has("new-paper"), false, "Entering the explicit review stage cancels an unsubmitted upload continuation");
  assert.match(complete.messages.join(" "), /主动选择 AI 识读/);
  assert.doesNotMatch(source, /采用此读法|\/apply-reading|查看待确认读法/);

  const missing = harness({ failure: true });
  const original = JSON.stringify(missing.context.state.questions);
  await missing.context.enterCutReadingStage("new-paper", [1, 2]);
  assert.equal(missing.requests.length, 0, "A missing AI service is irrelevant until the user chooses reading");
  assert.equal(JSON.stringify(missing.context.state.questions), original, "A missing service must keep all saved crop evidence");
  assert.equal(missing.nodes.pageDialog.open, false);

  const alreadyRead = harness({ questions: [imageQuestion(1, { ocr_suggestion: { revision: 5, stem: "Existing successful reading" } })] });
  await alreadyRead.context.enterCutReadingStage("new-paper", [1]);
  assert.equal(alreadyRead.requests.length, 0, "Legacy successful readings are never recharged by finishing cutting");

  const native = harness(); native.nodes.pageDialog.open = false;
  native.context.newUploadReadContinuations.add("new-paper");
  native.context.state.paper.status = "queued";
  await native.context.readNewlyCutUpload("new-paper");
  assert.equal(native.requests.length, 0);
  assert.equal(native.context.newUploadReadContinuations.has("new-paper"), true, "Wait for local cutting to finish");
  native.context.state.paper.status = "ready";
  await native.context.readNewlyCutUpload("new-paper");
  await native.context.readNewlyCutUpload("new-paper");
  assert.equal(native.requests.length, 1, "Repeated polls must not repeat this upload's reading");
  assert.deepEqual(native.requests[0].body.question_ids, [1, 2]);
  assert.equal(native.context.newUploadReadContinuations.has("new-paper"), false);

  const historic = harness(); historic.nodes.pageDialog.open = false;
  await historic.context.readNewlyCutUpload("new-paper");
  assert.equal(historic.requests.length, 0, "Opening a historic native paper cannot auto-read it");

  for (const assistant of [false, true]) {
    const noReader = harness({ reader: assistant ? "Service selected" : null });
    noReader.context.state.status.assistant_mode = assistant;
    noReader.context.newUploadReadContinuations.add("new-paper");
    await noReader.context.readNewlyCutUpload("new-paper");
    assert.equal(noReader.requests.length, 0);
    assert.match(noReader.context.cutReadingErrors.get("new-paper"), /设置 → 服务与密钥/);
    assert.equal(noReader.context.state.questions[0].body_mode, "source_image");
  }

  const failedNative = harness({ failure: true }); failedNative.nodes.pageDialog.open = false;
  failedNative.context.newUploadReadContinuations.add("new-paper");
  await failedNative.context.readNewlyCutUpload("new-paper");
  await failedNative.context.readNewlyCutUpload("new-paper");
  assert.equal(failedNative.requests.length, 1, "Failures leave crops available and require explicit retry, without polling charges");
  assert.match(failedNative.context.cutReadingErrors.get("new-paper"), /配置密钥/);

  const uploading = harness();
  await uploading.context.sendUpload({}, "new.pdf");
  assert.equal(uploading.context.newUploadReadContinuations.has("uploaded-paper"), true);
  uploading.context.newUploadReadContinuations.clear(); uploading.context.duplicate = true;
  await uploading.context.sendUpload({}, "duplicate.pdf");
  assert.equal(uploading.context.newUploadReadContinuations.size, 0, "Duplicate uploads are historic papers, not a new reading scope");

  console.log("Automatic saved-cut reading: closed dialog, exact batch, retained failures, no adoption, one-shot native continuation OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
