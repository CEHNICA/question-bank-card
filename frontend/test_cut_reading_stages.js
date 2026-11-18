"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Cut = require("./app.js");
const source = (id, extra = {}) => ({ id, body_mode: "source_image", content_revision: 4,
  regions: [{ page_idx: 0, bbox: [10, 20, 800, 300] }], ...extra });
const suggestion = { revision: 4, stem: "Find x.", error: "" };

assert.deepEqual(Cut.cutReadingSummary(), {
  saved: 0, pending: 0, done: 0, failed: 0, failedIds: [], remaining: 0, eligibleIds: [], revisions: {}, stage: 1,
});
const qs = [source(1), source(2, { ocr_pending: true }), source(3, { ocr_suggestion: suggestion }),
  source(4, { ocr_suggestion: { ...suggestion, revision: 3 } }),
  source(5, { ocr_suggestion: { ...suggestion, error: "Read failed" } }),
  source(6, { approved: true }), source(7, { publication: { up_to_date: true } }),
  source(8, { regions: [] }), source(9, { body_mode: "text" })];
const original = JSON.stringify(qs);
const summary = Cut.cutReadingSummary(qs);
assert.deepEqual(summary.eligibleIds, [1, 4, 5], "Retry stale/failed readings, but never duplicate pending or replace a successful suggestion");
assert.deepEqual(summary.revisions, { 1: 4, 4: 4, 5: 4 });
assert.equal(Object.hasOwn(summary, "suggestionIds"), false, "The normal review workflow has no separate adoption phase");
assert.equal(summary.pending, 1);
assert.equal(summary.done, 3);
assert.equal(summary.failed, 1);
assert.deepEqual(summary.failedIds, [5]);
assert.equal(summary.remaining, 3);
assert.equal(summary.stage, 2);
assert.equal(JSON.stringify(qs), original, "Entering the reading stage must not modify saved questions");
assert.equal(Cut.cutReadingSummary([source(1)]).stage, 2, "The second step remains discoverable after a reload");
assert.equal(Cut.cutReadingSummary([source(1, { ocr_suggestion: suggestion })]).stage, 3, "Legacy successful readings reach normal review without another paid request");
assert.equal(Cut.cutReadingSummary([source(1, { approved: true })]).stage, 3, "Image-only approval may skip AI reading");
assert.equal(Cut.hasCurrentReading(source(1, { ocr_suggestion: { ...suggestion, stem: " " } })), false);
assert.deepEqual(Cut.cutReadingRequest(qs, [1, 4], 5), { question_ids: [1, 4], revisions: { 1: 4, 4: 4 }, revision: 5 });
const manualText = source(10, { body_mode: "text", processing_mode: "manual", stem: "Saved reviewed text", ocr_pending: true });
assert.deepEqual(Cut.cutReadingSummary([manualText]), {
  saved: 0, pending: 1, done: 0, failed: 0, failedIds: [], remaining: 0, eligibleIds: [], revisions: {}, stage: 2,
}, "Manual text rereading remains stoppable without becoming a new batch candidate");
assert.equal(Cut.cutReadingSummary([{ ...manualText, ocr_pending: false }]).stage, 3, "Successful text returns to ordinary review");
assert.equal(Cut.cutReadingSummary([{ ...manualText, processing_mode: "auto" }]).pending, 0, "Unrelated automatic text reading keeps its existing workflow");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const entry = js.slice(js.indexOf("  async function enterCutReadingStage("), js.indexOf("  async function readCutQuestions("));
assert.match(entry, /await refreshPaper\(\)/);
assert.doesNotMatch(entry, /await readCutQuestions\(questionIds\)/, "Completing cutting does not automatically start saved-cut reading");
assert.match(entry, /主动选择 AI 识读或直接原图审核/);
assert.match(js, /重新识读失败题目（\$\{summary\.failedIds\.length\}）/);
assert.match(js, /识读未完成题目（\$\{summary\.eligibleIds\.length\} 题）/);
assert.match(js, /正在识读：已完成 \$\{summary\.done\} \/ \$\{summary\.saved\}/);
assert.match(html, /id="cutReadingProgress"/);
assert.doesNotMatch(js, /采用此读法|\/apply-reading|查看待确认读法|读法待确认/, "There is no separate adoption action");
assert.match(js, /\/api\/papers\/\$\{paperId\}\/read-cut-questions/);
assert.match(js, /body: \{ revision: q\.content_revision \}, signal/);
assert.doesNotMatch(js, /if \(q\.body_mode === "source_image"\) openPageDialog\("regions", q\); else rereadQuestion/);
assert.doesNotMatch(html, /id="addQuestion"|漏了一题？手动框出|从原卷补题<\/button>/);
assert.match(html, /id="viewOriginalPaper"[^>]*>切题与校正<\/button>/);
assert.match(html, /id="cutReadingStage"/);
assert.match(html, /id="cutReadingSettings" href="\/settings#services"/);
assert.match(js, /settingsGeneral: "services"/);
assert.match(js, /if \(state\.paper\) renderCutReadingStage\(\)/, "Saving credentials must redraw the reading stage");

(async () => {
  // Execute the actual submission handler: selected IDs and revisions must have
  // exactly the same scope, as required by the backend batch contract.
  const handler = js.slice(js.indexOf("  async function readCutQuestions("), js.indexOf("  function setFilter("));
  const requests = [];
  const context = { state: { paperId: "paper-test", paper: { status: "ready", processing_plan: { revision: 5 } }, questions: qs },
    cutReadingRequests: new Set(), cutReadingStops: new Set(), cutReadingErrors: new Map(), cutReadingStopErrors: new Map(), directImageReview: new Set(), QBCutReading: Cut,
    questionReadingRequests: new Set(),
    QBRegionWait: { boundedRequest: (task) => task(undefined) },
    paperReadSubmissionPending: () => context.cutReadingRequests.has(context.state.paperId)
      || context.cutReadingStops.has(context.state.paperId)
      || context.state.questions.some((q) => context.questionReadingRequests.has(q.id)),
    renderReadingControls() {}, refreshPaper: async () => {}, toast() {},
    api: async (url, options) => { requests.push({ url, body: JSON.parse(JSON.stringify(options.body)) }); return { queued: 1 }; } };
  await vm.runInNewContext(handler + "\nreadCutQuestions([1]);", context);
  assert.deepEqual(requests, [{ url: "/api/papers/paper-test/read-cut-questions", body: { question_ids: [1], revisions: { 1: 4 }, revision: 5 } }]);
  await vm.runInNewContext(handler + "\nreadCutQuestions([2, 3, 6, 7, 8, 9]);", context);
  assert.equal(requests.length, 1, "Pending, already read, approved, published or unsaved questions never start a new reading");
  context.questionReadingRequests.add(1);
  await vm.runInNewContext(handler + "\nreadCutQuestions([4]);", context);
  assert.equal(requests.length, 1, "Batch reading waits while a single submission is in flight");
  context.questionReadingRequests.clear();

  // 兼容未重启的旧后端：批量接口误将带撤回记录的题目跳过时，自动回退逐题调用 reread；
  // 遇到 SQLite 锁冲突导致的 500 临时错误应退避重试而非直接中断整批。
  const fallbackRequests = [];
  const fallbackToasts = [];
  let q1Attempts = 0;
  const fallbackContext = {
    ...context,
    setTimeout: (fn) => fn(),
    toast: (message, kind) => fallbackToasts.push({ message, kind }),
    api: async (url, options) => {
      fallbackRequests.push({ url, body: JSON.parse(JSON.stringify(options.body)) });
      if (url.endsWith("/read-cut-questions")) {
        return {
          queued: 0,
          skipped: [
            { id: 1, reason: "已审核或有入库记录，已保留" },
            { id: 4, reason: "已审核或有入库记录，已保留" },
            { id: 5, reason: "已有人工修改的文字，已保留" },
          ],
          message: "没有新的题目需要识读；可直接查看、审核已有结果。",
        };
      }
      if (url === "/api/questions/1/reread") {
        q1Attempts += 1;
        if (q1Attempts === 1) {
          const err = new Error("Internal Server Error");
          err.status = 500;
          throw err;
        }
        return { question: { id: 1 } };
      }
      if (url === "/api/questions/4/reread") {
        const err = new Error("已保留");
        err.status = 409;
        throw err;
      }
      return { question: { id: 1 } };
    },
  };
  await vm.runInNewContext(handler + "\nreadCutQuestions([1, 4, 5]);", fallbackContext);
  assert.deepEqual(fallbackRequests, [
    { url: "/api/papers/paper-test/read-cut-questions", body: { question_ids: [1, 4, 5], revisions: { 1: 4, 4: 4, 5: 4 }, revision: 5 } },
    { url: "/api/questions/1/reread", body: { revision: 4 } },
    { url: "/api/questions/1/reread", body: { revision: 4 } },
    { url: "/api/questions/4/reread", body: { revision: 4 } },
  ]);
  assert.deepEqual(fallbackToasts, [{ message: "已提交 1 道已切题目，识读完成后可直接审核。", kind: "success" }]);

  const single = js.slice(js.indexOf("  async function rereadQuestion("), js.indexOf("  function updatePaperFromResponse("));
  context.questionById = (id) => qs.find((q) => q.id === id);
  context.applyQuestion = () => {};
  context.q = qs[0];
  context.cutReadingRequests.add("paper-test");
  await vm.runInNewContext(single + "\nrereadQuestion(q);", context);
  assert.equal(requests.length, 1, "Single reading waits while a batch submission is in flight");
  context.cutReadingRequests.clear();
  context.cutReadingErrors.set("paper-test", "Configure a reader");
  await vm.runInNewContext(single + "\nrereadQuestion(q);", context);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].url, "/api/questions/1/reread");
  assert.deepEqual(requests[1].body, { revision: 4 });
  assert.equal(context.cutReadingErrors.has("paper-test"), false, "A successful single request clears an older batch configuration failure");
  for (const status of ["failed", "queued", "parsing", "reading", "needs_grouping"]) {
    context.state.paper.status = status;
    await vm.runInNewContext(handler + "\nreadCutQuestions([1]);", context);
    await vm.runInNewContext(single + "\nrereadQuestion(q);", context);
    assert.equal(requests.length, 2, `${status} papers must recover before single or batch image reading`);
  }
  assert.match(js, /请先继续手工整理或重试恢复这份资料，再开始 AI 识读/);
  assert.equal(JSON.stringify(qs), original, "Submitting readings keeps the saved originals intact");

  // Live readiness unlocks the batch button even when cards still carry a stale
  // reader_not_configured suggestion from an earlier failed attempt.
  const stage = js.slice(js.indexOf("  function latestReaderConfigFailure("), js.indexOf("  function openManualCut("));
  const stageNodes = new Map(), stageButtons = [];
  const stageNode = () => {
    const classes = new Set();
    return {
      hidden: false, dataset: {}, children: [], style: {}, textContent: "",
      append(...items) { this.children.push(...items); },
      replaceChildren(...items) { this.children = items; },
      setAttribute() {},
      classList: {
        add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
        toggle(name, force) { const add = force ?? !classes.has(name); if (add) classes.add(name); else classes.delete(name); },
      },
    };
  };
  const failedCard = source(11, {
    ocr_suggestion: {
      revision: 4,
      error: "本机还没有配置任何看图读题服务：所选的“MiniMax”没有 API Key，也没有其他已配置的服务。",
      error_kind: "reader_not_configured",
      error_service_label: "MiniMax",
    },
  });
  const unlockContext = {
    state: {
      paperId: "unlock-paper",
      paper: { id: "unlock-paper", parse_mode: "manual", status: "ready" },
      questions: [failedCard],
      status: { reader_readiness: { ready: true, label: "MiniMax-M3" } },
    },
    QBCutReading: Cut,
    cutReadingErrors: new Map(), cutReadingStopErrors: new Map(), directImageReview: new Set(),
    cutReadingRequests: new Set(), cutReadingStops: new Set(),
    manualSwitches: new Set(), aiCutContinuations: new Set(),
    paperReadSubmissionPending: () => false,
    readerUnavailable: () => null,
    readerUnavailableSentence: () => "not ready",
    $: (id) => { if (!stageNodes.has(id)) stageNodes.set(id, stageNode()); return stageNodes.get(id); },
    document: { createTextNode: (text) => text }, el: () => stageNode(),
    button: (label) => { const item = stageNode(); item.label = label; item.disabled = false; stageButtons.push(item); return item; },
    stopCutReading() {}, openManualCut() {}, focusCutReview() {}, readCutQuestions() {},
  };
  vm.runInNewContext(stage + "\nrenderCutReadingStage();", unlockContext);
  const retry = stageButtons.find((item) => /重新识读失败题目（1）/.test(item.label));
  assert.ok(retry, "A stale config failure must become a retry action once live readiness is true");
  assert.equal(retry.disabled, false, "Live ready must not keep the batch button locked");
  assert.match(stageNodes.get("cutReadingTitle").textContent, /1 道识读失败，可重试/);
  assert.match(unlockContext.cutReadingErrors.get("unlock-paper"), /现可重试/);

  const blockedButtons = [];
  const blockedContext = {
    ...unlockContext,
    cutReadingErrors: new Map(),
    readerUnavailable: () => ({ ready: false, label: "MiniMax" }),
    readerUnavailableSentence: (notReady) => `还没有可用的看图读题模型：所选的“${notReady.label}”还没有 API Key`,
    button: (label) => { const item = stageNode(); item.label = label; item.disabled = false; blockedButtons.push(item); return item; },
  };
  vm.runInNewContext(stage + "\nrenderCutReadingStage();", blockedContext);
  const blocked = blockedButtons.find((item) => /重新识读失败题目|识读未完成题目/.test(item.label));
  assert.ok(blocked);
  assert.equal(blocked.disabled, true, "Live unreadiness still disables the batch button");
  assert.match(blockedContext.cutReadingErrors.get("unlock-paper"), /还没有 API Key/);

  // 批量识读进行中，已完成的题目会被后端 _cut_to_text 转为 text/manual：
  // 此时分母 saved 必须保持总题数不变，done 计入已转文字题卡，标题显示「正在识读：已完成 X / Total」。
  const mixedBatch = [
    source(21, { body_mode: "text", processing_mode: "manual", stem: "已完成第 1 题" }),
    source(22, { body_mode: "text", processing_mode: "manual", stem: "已完成第 2 题", approved: true }),
    source(23, { ocr_pending: true }),
    source(24),
  ];
  const mixedSummary = Cut.cutReadingSummary(mixedBatch);
  assert.equal(mixedSummary.saved, 4, "总题数应包含已转文字的切题卡片");
  assert.equal(mixedSummary.done, 2, "已转文字且不在识读中的题卡应计入已完成数");
  assert.equal(mixedSummary.pending, 1);
  assert.equal(mixedSummary.remaining, 1);
  assert.deepEqual(mixedSummary.eligibleIds, [24], "已转文字的题卡不应重复进入待识读候选");
  const mixedNodes = new Map();
  const mixedContext = {
    ...unlockContext,
    state: {
      paperId: "mixed-paper",
      paper: { id: "mixed-paper", parse_mode: "manual", status: "ready" },
      questions: mixedBatch,
      status: { reader_readiness: { ready: true, label: "MiniMax-M3" } },
    },
    cutReadingErrors: new Map(),
    $: (id) => { if (!mixedNodes.has(id)) mixedNodes.set(id, stageNode()); return mixedNodes.get(id); },
    button: (label) => { const item = stageNode(); item.label = label; item.disabled = false; return item; },
  };
  vm.runInNewContext(stage + "\nrenderCutReadingStage();", mixedContext);
  assert.equal(mixedNodes.get("cutReadingTitle").textContent, "正在识读：已完成 2 / 4");

  // 带 manual_text 护栏的原图题不进入待识读候选，必须计入 done，使「待识读」与标题未识读数一致。
  const guardedBatch = [
    source(31, { body_mode: "text", processing_mode: "manual", stem: "已识读" }),
    source(32, { cut_reading_guard: { blocked: true, reason: "manual_text" } }),
    source(33),
  ];
  const guardedSummary = Cut.cutReadingSummary(guardedBatch);
  assert.equal(guardedSummary.saved, 3);
  assert.equal(guardedSummary.done, 2);
  assert.equal(guardedSummary.remaining, 1);
  assert.equal(guardedSummary.saved - guardedSummary.done - guardedSummary.pending - guardedSummary.failed, guardedSummary.remaining);

  console.log("Saved-cut reading eligibility, normal review, subset contract and submission guards: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
