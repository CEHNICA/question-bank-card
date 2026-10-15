"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const test = require("node:test"), App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8"), html = fs.readFileSync(require.resolve("./index.html"), "utf8");
const helper = source.slice(source.indexOf("  const manualSwitches = new Set();"), source.indexOf("  async function switchToManual("));
const render = source.slice(source.indexOf("  function renderSettingsTask()"), source.indexOf("  function closeSettingsThen(action)"));
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(value => { resolve = value; }); return { promise, resolve }; };
const settle = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const paper = (extra = {}) => ({ id: "paper", parse_mode: "manual", status: "ready", pages: [{ page_idx: 0 }], counts: {}, processing_plan: { mode: "manual", revision: 7 }, ...extra });
function harness(options = {}) {
  const nodes = new Map(), requests = [], confirmations = [], notices = [], updates = [], refreshes = [], stageEntryStates = [];
  let sidebarRefreshes = 0;
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, { id, hidden: false, disabled: false, events: {}, addEventListener(name, fn) { this.events[name] = fn; }, replaceChildren() {} });
    return nodes.get(id);
  };
  const context = { $, QBProgress: App, ACTIVE_STATUS: new Set(["queued", "parsing", "segmenting", "reading"]),
    state: { paperId: "paper", paper: options.paper || paper(), papers: [], questions: options.questions || [{ id: 21, stem: "已保存人工题", answer: "B", figures: [{ slot: "A", file: "keep.png" }], approved: true, publication: { id: "old-pub", up_to_date: true } }] },
    newUploadReadContinuations: new Set(["paper"]), paperReadSubmissionPending: () => Boolean(options.readSubmissionPending),
    syncTrashControls() {}, suggestedSplitGroups: () => [], paperSummary: () => "状态", el: () => ({}),
    renderCutReadingStage() { stageEntryStates.push(Boolean(context.aiBusy?.has(context.state.paperId) || context.manualBusy?.has(context.state.paperId))); },
    toast: (text, kind) => notices.push({ text, kind }),
    confirmDialog: async value => { confirmations.push(clone(value)); if (options.confirm) return options.confirm(context); return options.confirmed !== false; },
    QBRegionWait: { boundedRequest: async (fn, config) => {
      assert.equal(config.timeoutMs, 30000);
      if (options.timeout) { const error = Error("timeout"); error.name = "TimeoutError"; throw error; }
      return fn(undefined);
    } },
    api: async (url, args) => {
      requests.push({ url, method: args.method, body: clone(args.body) });
      if (options.api) return options.api(context);
      if (options.failure) throw Error(options.failure);
      return options.response || { paper: paper({ parse_mode: "mineru", status: "segmenting", processing_plan: { mode: "mineru", revision: 8 } }), action: "local_segmentation", changed: true, message: "已使用本机已有解析，保留已保存题目。" };
    },
    updatePaperFromResponse: value => { updates.push(clone(value)); context.state.paper = value; }, renderPaper() {},
    refreshPaper: async () => { refreshes.push(context.state.paperId); return true; }, loadPapers: async () => { sidebarRefreshes++; } };
  vm.runInNewContext(helper + render + "\nglobalThis.aiBusy = aiCutContinuations;globalThis.manualBusy = manualSwitches;", context);
  return { context, $, requests, confirmations, notices, updates, refreshes, stageEntryStates, sidebar: () => sidebarRefreshes };
}
test("menu recovery and menu AI continuation stay available while MinerU waits; continuing submits nothing", async () => {
  assert.match(html, /id="paperContinueAi"[^>]*>继续 AI 切题<\/button>/);
  assert(html.indexOf('id="paperContinueAi"') > html.indexOf('id="paperMenu"'));
  assert(html.indexOf('id="paperContinueAi"') < html.indexOf('id="cutReadingStage"'));
  assert.doesNotMatch(html, /paperManualEntry|paperManualFallback/, "1.12.6: both cut-mode switches live in 试卷操作 only");
  for (const status of ["queued", "parsing", "segmenting"]) {
    const h = harness({ paper: paper({ parse_mode: "mineru", status, processing: { stage: status, mineru: { state: "pending", for_seconds: 90 } } }) });
    const original = clone(h.context.state); h.context.renderSettingsTask();
    assert.equal(h.$("settingsManualFallback").hidden, false); assert.equal(h.$("paperContinueAi").hidden, false);
    assert.equal(await h.context.continueAiCut(), true);
    assert.equal(h.requests.length, 0); assert.equal(h.confirmations.length, 0); assert.equal(h.refreshes.length, 0); assert.equal(h.sidebar(), 0);
    assert.deepEqual(clone(h.context.state), original); assert(h.notices[0].text.includes("不会重新提交"));
  }
});
test("manual, native and stopped pages visibly offer AI continuation without duplicating manual buttons", async () => {
  for (const value of [paper(), paper({ parse_mode: "native" }), paper({ status: "failed" }), paper({ parse_mode: "mineru", status: "failed", stopped: true })]) {
    const h = harness({ paper: value }); h.context.renderSettingsTask();
    assert.equal(h.$("settingsManualFallback").hidden, true, "Manual-ready papers have one stage action, with AI continuation in the menu"); assert.equal(h.$("paperContinueAi").hidden, false);
  }
  for (const value of [null, paper({ archived: true }), paper({ demo: true }), paper({ status: "needs_grouping" }), paper({ parse_mode: "mineru", status: "reading" }), paper({ parse_mode: "mineru", status: "ready" })]) {
    assert.equal(App.canContinueAiCut(value), false);
    if (value) { const h = harness({ paper: value }); h.context.renderSettingsTask(); assert.equal(await h.context.continueAiCut(), false); assert.equal(h.requests.length, 0); }
  }
});
test("confirmed continuation sends the precise revision once and preserves every stored question", async () => {
  const h = harness(), original = clone(h.context.state.questions);
  assert.equal(await h.context.continueAiCut(), true);
  assert.deepEqual(h.requests, [{ url: "/api/papers/paper/continue-ai-cut", method: "POST", body: { revision: 7, allow_cloud: true } }]);
  assert(h.confirmations[0].text.includes("原卷、已保存的题目、手工范围和修改")); assert(h.confirmations[0].text.includes("已通过或入库"));
  assert(h.confirmations[0].text.includes("整份原稿")); assert(h.confirmations[0].text.includes("可能使用服务额度")); assert(h.confirmations[0].text.includes("旧任务不会被当作可续用"));
  assert.equal(h.context.newUploadReadContinuations.has("paper"), false, "Old upload-reading continuation must not queue saved manual questions");
  assert.deepEqual(clone(h.context.state.questions), original); assert.equal(h.updates[0].parse_mode, "mineru"); assert.equal(h.refreshes.length, 1); assert.equal(h.sidebar(), 1);
  assert.equal(h.context.aiBusy.size, 0); assert.equal(h.notices.at(-1).kind, "success");
});
test("declining and stale confirmation create no request and retain the manual state", async () => {
  const decline = harness({ confirmed: false }); assert.equal(await decline.context.continueAiCut(), false); assert.equal(decline.requests.length, 0);
  assert.equal(decline.context.newUploadReadContinuations.has("paper"), true); assert.equal(decline.context.state.paper.parse_mode, "manual");
  const stale = harness({ confirm: context => { context.state.paper.processing_plan.revision++; return true; } });
  assert.equal(await stale.context.continueAiCut(), false); assert.equal(stale.requests.length, 0); assert(stale.notices.at(-1).text.includes("已经变化"));
  const moved = harness({ confirm: context => { context.state.paperId = "other"; context.state.paper = paper({ id: "other" }); return true; } });
  assert.equal(await moved.context.continueAiCut(), false); assert.equal(moved.requests.length, 0); assert.equal(moved.notices.length, 0);
});
test("confirmation and submission lock repeat clicks and conflicting manual/stop actions", async () => {
  const confirmGate = deferred(), requestGate = deferred();
  const h = harness({ confirm: () => confirmGate.promise, api: async () => { await requestGate.promise; return { paper: paper({ parse_mode: "mineru", status: "queued" }), action: "queued_mineru", changed: true }; } });
  const first = h.context.continueAiCut(); await settle();
  assert.equal(h.confirmations.length, 1); assert.equal(await h.context.continueAiCut(), false);
  for (const id of ["paperContinueAi", "settingsStop", "settingsReparse", "viewOriginalPaper", "pageManualCut", "emptyManualCut"]) assert.equal(h.$(id).disabled, true);
  assert.deepEqual(h.stageEntryStates, [true], "Existing cutting-stage actions must be redrawn while confirmation is pending");
  confirmGate.resolve(true); await settle(); assert.equal(h.requests.length, 1); assert.equal(await h.context.continueAiCut(), false);
  requestGate.resolve(); await first; assert.equal(h.requests.length, 1); assert.equal(h.context.aiBusy.size, 0);
  assert.equal(h.$("paperContinueAi").disabled, false);
  assert.equal(h.$("emptyManualCut").disabled, false);
  assert.deepEqual(h.stageEntryStates, [true, false], "All stage and empty-page entries must be restored together after POST");
  const manual = harness(); manual.context.manualBusy.add("paper"); assert.equal(await manual.context.continueAiCut(), false); assert.equal(manual.confirmations.length, 0);
});
test("pending saved-question reading blocks continuation without discarding text or suggesting a fresh cloud task", async () => {
  for (const options of [{ readSubmissionPending: true }, { questions: [{ id: 3, ocr_pending: true }] }, { questions: [{ id: 3, reread_requested: true }] }]) {
    const h = harness(options), original = clone(h.context.state.questions); h.context.renderSettingsTask();
    assert.equal(h.$("paperContinueAi").disabled, true); assert.equal(await h.context.continueAiCut(), false);
    assert.equal(h.requests.length, 0); assert.equal(h.confirmations.length, 0); assert.deepEqual(clone(h.context.state.questions), original);
    assert(h.notices.at(-1).text.includes("正在识读"));
  }
});
test("failure, missing credentials, timeout and malformed responses preserve all work and release retry controls", async () => {
  for (const options of [{ failure: "原卷版本已变化，未改动任何题目" }, { failure: "请先配置 MinerU Token" }, { timeout: true }, { response: { paper: paper(), changed: true } }]) {
    const h = harness(options), original = clone(h.context.state.questions);
    assert.equal(await h.context.continueAiCut(), false); assert.equal(h.updates.length, 0); assert.deepEqual(clone(h.context.state.questions), original);
    assert.equal(h.context.aiBusy.size, 0); assert.equal(h.$("paperContinueAi").disabled, false); assert.equal(h.$("emptyManualCut").disabled, false); assert.deepEqual(h.stageEntryStates, [true, false]); assert.equal(h.notices.at(-1).kind, "error"); assert.equal(h.refreshes.length, 1);
    if (options.timeout) assert(h.notices.at(-1).text.includes("提交结果尚未确认"), "A timeout must not falsely claim it cancelled a submitted server job");
  }
});
test("a late continuation response cannot replace a newly selected paper or its notices", async () => {
  const gate = deferred(), h = harness({ api: async () => { await gate.promise; return { paper: paper({ parse_mode: "mineru", status: "queued" }), changed: true }; } });
  const operation = h.context.continueAiCut(); await settle();
  h.context.state.paperId = "other"; h.context.state.paper = paper({ id: "other", processing_plan: { revision: 21 } });
  gate.resolve(); assert.equal(await operation, false);
  assert.equal(h.context.state.paper.id, "other"); assert.equal(h.updates.length, 0); assert.equal(h.notices.length, 0); assert.equal(h.refreshes.length, 0); assert.equal(h.sidebar(), 0);
  assert.equal(h.context.aiBusy.size, 0);
});
test("both first-step and later quiet manual stage buttons obey continuation busy state", () => {
  const stage = source.slice(source.indexOf("  function renderCutReadingStage()"), source.indexOf("  function openManualCut()"));
  for (const stageNumber of [1, 3]) {
    const nodes = new Map(), node = () => ({ dataset: {}, children: [], append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; }, setAttribute() {} });
    const context = { state: { paper: paper(), questions: [] }, QBCutReading: { showCutReadingStage: App.showCutReadingStage, cutReadingSummary: () => ({ stage: stageNumber, saved: 1, pending: 0, eligibleIds: [] }) },
      cutReadingErrors: new Map(), cutReadingStopErrors: new Map(), directImageReview: new Set(), cutReadingRequests: new Set(), cutReadingStops: new Set(),
      manualSwitches: new Set(), aiCutContinuations: new Set(["paper"]), paperReadSubmissionPending: () => false,
      $: id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); }, document: { createTextNode: text => text }, el: node,
      button: label => ({ ...node(), label }), openManualCut() {}, focusCutReview() {}, readCutQuestions() {}, stopCutReading() {} };
    vm.runInNewContext(stage, context); context.renderCutReadingStage();
    const manualButton = () => nodes.get("cutReadingActions").children.find(button => /切题与校正|正在准备原卷/.test(button.label));
    assert.equal(manualButton().disabled, true, `Cutting stage ${stageNumber} must disable its manual button`);
    context.aiCutContinuations.clear(); context.renderCutReadingStage(); assert.equal(manualButton().disabled, false);
    context.manualSwitches.add("paper"); context.renderCutReadingStage(); assert.equal(manualButton().disabled, true);
  }
});
