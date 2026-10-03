"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
const switchCode = source.slice(source.indexOf("  const manualSwitches = new Set();"), source.indexOf("  const resegmentPreview ="));
const render = source.slice(source.indexOf("  function renderSettingsTask()"), source.indexOf("  function closeSettingsThen(action)"));
const listener = source.slice(source.indexOf('  $("settingsManualFallback").addEventListener'), source.indexOf('  $("renameNudge").addEventListener'));
const clone = (value) => JSON.parse(JSON.stringify(value));
const cloudPaper = (extra = {}) => ({ id: "paper", parse_mode: "mineru", status: "parsing", pages: [], counts: {}, ...extra });
const manualPaper = (extra = {}) => cloudPaper({ parse_mode: "manual", status: "ready", pages: [{ page_idx: 0 }, { page_idx: 3 }], ...extra });

function harness(options = {}) {
  const nodes = new Map(), calls = [], openings = [], messages = [], order = [], frames = [];
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, { id, open: false, hidden: false, disabled: false, events: {},
      addEventListener(name, callback) { this.events[name] = callback; }, replaceChildren() {},
      close() { this.open = false; order.push("close"); } });
    return nodes.get(id);
  };
  const context = {
    pageOpenIntent: 0, AbortController,
    state: { paperId: "paper", paper: options.paper || cloudPaper(), papers: [], questions: [{ id: 8, stem: "Existing edited question", figures: ["unchanged"], approved: true }] },
    QBProgress: App, ACTIVE_STATUS: new Set(["queued", "parsing", "segmenting", "reading"]),
    newUploadReadContinuations: new Set(["paper"]), $,
    paperReadSubmissionPending: () => false,
    syncTrashControls() {}, suggestedSplitGroups: () => [], paperSummary: () => "解析中", el: () => ({}),
    paperReadSubmissionPending: () => false,
    renderCutReadingStage() { order.push("entries"); },
    closePageDialog() { $("pageDialog").close(); },
    toast: (message, kind) => messages.push({ message, kind }),
    updatePaperFromResponse: (paper) => { context.state.paper = paper; order.push("update"); },
    refreshPaper: async () => {
      order.push("refresh");
      if (options.refresh) return options.refresh(context);
      return options.refreshed !== false;
    },
    openPageDialog: (...args) => { openings.push(clone(args)); order.push("open"); },
    QBRegionWait: { boundedRequest: async (task, config) => {
      assert.equal(config.timeoutMs, 30000, "A missing local response cannot lock the switch indefinitely");
      if (options.timeout) { const error = new Error("timed out"); error.name = "TimeoutError"; throw error; }
      return task(undefined);
    } },
    api: async (url, args) => {
      calls.push({ url, method: args.method, body: clone(args.body) }); order.push("request");
      if (options.api) return options.api(context);
      if (options.failure) throw Error("原文件无法准备，已有成果保留");
      return options.response || { paper: manualPaper(), manual_ready: true, changed: true };
    },
    closeSettingsThen: (callback) => { frames.push(callback); $("paperMenu").open = false; }
  };
  vm.runInNewContext(switchCode + render + listener + "\nglobalThis.busy = manualSwitches;", context);
  return { context, $, nodes, calls, openings, messages, order, frames };
}

(async () => {
  assert.match(html, /id="settingsManualFallback"[^>]*hidden[^>]*>停止 MinerU，改为手工切题<\/button>/);
  assert.ok(html.indexOf('id="settingsManualFallback"') < html.indexOf('id="settingsStop"'));
  for (const status of ["queued", "parsing", "segmenting"]) {
    const visible = harness({ paper: cloudPaper({ status }) }); visible.context.renderSettingsTask();
    assert.equal(visible.$("settingsManualFallback").hidden, false, `MinerU ${status} offers the direct switch`);
    assert.equal(visible.$("paperManualEntry").hidden, false, `${status} also exposes the primary recovery action beside the status, without opening a menu`);
    assert.equal(visible.$("paperManualFallback").textContent, "改为手工切题");
    assert.match(visible.$("paperManualHint").textContent, /停止等待.*已有题目保留/);
  }
  for (const paper of [cloudPaper({ parse_mode: "native" }), cloudPaper({ parse_mode: "manual" }),
    cloudPaper({ parse_mode: "auto", status: "queued" }), cloudPaper({ status: "reading" }),
    cloudPaper({ status: "ready" }), cloudPaper({ archived: true }), cloudPaper({ demo: true })]) {
    const hidden = harness({ paper }); hidden.context.renderSettingsTask();
    assert.equal(hidden.$("settingsManualFallback").hidden, true, "Other routes/stages must not be mislabeled MinerU");
    assert.equal(hidden.$("paperManualEntry").hidden, !App.canContinueAiCut(paper));
    assert.equal(await hidden.context.switchToManual(null, { stopMinerU: true }), false);
    assert.equal(hidden.calls.length, 0);
  }

  const success = harness(); const before = clone(success.context.state.questions);
  assert.equal(await success.context.switchToManual(null, { stopMinerU: true }), true);
  assert.deepEqual(success.calls, [{ url: "/api/papers/paper/processing", method: "POST", body: { mode: "manual" } }]);
  assert.deepEqual(success.openings, [["new", null, { page: null }]], "Switching opens the cutting canvas directly without another user choice");
  assert.deepEqual(success.order.filter(item => item !== "entries"), ["request", "update", "refresh", "open"]);
  assert.equal(success.order.filter(item => item === "entries").length, 2, "Ready papers release the cutting entry's busy state without waiting for another poll");
  assert.deepEqual(success.context.state.questions, before, "Existing edited/approved content and figures are kept");
  assert.equal(success.context.newUploadReadContinuations.has("paper"), false, "Switching never starts automatic reading");
  assert.equal(success.context.busy.size, 0);
  assert.equal(success.$("settingsManualFallback").hidden, true, "The cloud-only action disappears after conversion");
  assert.equal(success.$("paperManualEntry").hidden, false, "The saved manual pages keep a visible continuation back to automatic cutting");
  assert.equal(success.$("paperContinueAi").hidden, false);
  const alreadyManual = harness({ response: { paper: manualPaper(), manual_ready: true, changed: false } });
  assert.equal(await alreadyManual.context.switchToManual(null, { stopMinerU: true }), true);
  assert.equal(alreadyManual.openings.length, 1, "A concurrently completed, idempotent switch still opens the saved manual pages");

  let release;
  const pending = harness({ api: () => new Promise((resolve) => { release = resolve; }) });
  const first = pending.context.switchToManual(null, { stopMinerU: true });
  assert.equal(pending.$("settingsManualFallback").disabled, true);
  for (const id of ["settingsStop", "settingsReparse", "manualProcessing", "pageManualCut", "paperManualFallback"]) assert.equal(pending.$(id).disabled, true);
  assert.equal(pending.$("paperManualEntry").hidden, false);
  assert.match(pending.$("paperManualFallback").textContent, /正在准备/);
  assert.equal(await pending.context.switchToManual(null, { stopMinerU: true }), false);
  assert.equal(pending.calls.length, 1, "Repeated clicks share the one in-flight switch");
  release({ paper: manualPaper(), manual_ready: true }); await first;
  assert.equal(pending.openings.length, 1);
  assert.equal(pending.$("settingsStop").disabled, false);
  assert.equal(pending.$("paperManualFallback").disabled, false);
  assert.equal(pending.$("paperManualEntry").hidden, false);
  assert.equal(pending.$("paperManualFallback").hidden, true, "The manual action stays in the cutting stage instead of being duplicated in the continuation card");

  for (const options of [{ failure: true }, { timeout: true }, { refreshed: false },
    { response: { paper: manualPaper(), manual_ready: false } },
    { response: { paper: cloudPaper(), manual_ready: true } },
    { response: { paper: manualPaper({ pages: [] }), manual_ready: true } }]) {
    const failed = harness(options); const saved = clone(failed.context.state.questions);
    assert.equal(await failed.context.switchToManual(null, { stopMinerU: true }), false);
    assert.equal(failed.openings.length, 0, "Failures or unconfirmed original pages cannot open an unusable crop canvas");
    assert.equal(failed.messages.at(-1).kind, "error");
    assert.deepEqual(failed.context.state.questions, saved);
    assert.equal(failed.context.busy.size, 0, "Failure releases the switch for retry");
    assert.ok(failed.order.includes("refresh"));
  }

  let finishStale;
  const changed = harness({ api: () => new Promise((resolve) => { finishStale = resolve; }) });
  const stale = changed.context.switchToManual(null, { stopMinerU: true });
  changed.context.state.paperId = "different";
  changed.context.state.paper = cloudPaper({ id: "different", parse_mode: "native", status: "ready" });
  finishStale({ paper: manualPaper(), manual_ready: true }); await stale;
  assert.equal(changed.context.state.paper.id, "different");
  assert.equal(changed.openings.length, 0, "A late response does not replace or open a different selected paper");

  const navigating = harness({ refresh: async (context) => {
    context.state.paperId = "different"; context.state.paper = cloudPaper({ id: "different", status: "ready" }); return true;
  } });
  await navigating.context.switchToManual(null, { stopMinerU: true });
  assert.equal(navigating.openings.length, 0, "Navigation during the post-switch refresh also prevents the old dialog from opening");

  const queuedClick = harness(); queuedClick.$("settingsManualFallback").events.click();
  queuedClick.context.state.paperId = "different";
  queuedClick.frames[0]();
  assert.equal(queuedClick.calls.length, 0, "A delayed menu close cannot apply the user's old click to the newly selected paper");

  const direct = harness();
  direct.$("paperManualFallback").events.click();
  assert.equal(direct.calls.length, 1, "The visible recovery button immediately calls the same guarded local transition, without a menu or confirmation");
  assert.equal(direct.frames.length, 0);

  const existing = harness({ paper: cloudPaper({ parse_mode: "manual", status: "failed" }) });
  assert.equal(await existing.context.switchToManual(3), true);
  assert.deepEqual(existing.calls[0].body, { mode: "manual" }, "A page number positions the UI and is never sent as an unsupported cloud page restriction");
  assert.deepEqual(existing.openings, [["new", null, { page: 3 }]]);

  console.log("MinerU to manual: route/stage visibility, one-step canvas opening, preserved questions, no OCR, bounded failure/retry, click deduplication and stale navigation guards: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
