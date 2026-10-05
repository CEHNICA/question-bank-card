"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const dom = require("./credential-test-dom.js");
const App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const css = fs.readFileSync(require.resolve("./styles.css"), "utf8");
assert.match(css, /\.credential-dialog::backdrop\s*\{\s*backdrop-filter:\s*none;/);
const between = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const functions = between("  const CREDENTIAL_FIELDS = {", "  function renderSettingsTask(");
const start = source.indexOf('  Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {', source.indexOf('  $("questionTrash").addEventListener("click", openQuestionTrash);'));
const listeners = source.slice(start, source.indexOf("  // Queue the values captured", start));
const apiFunction = between("  async function api(", "  let toastTimer =");
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { resolve, reject, promise }; };
const turns = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const response = (body, ok = true) => ({ ok, status: ok ? 200 : 409, json: async () => body });
const copy = (value) => JSON.parse(JSON.stringify(value));

function scenario() {
  const nodes = new Map(), calls = [], timers = new Map(), closes = [], events = {}, windows = {};
  const counts = { mineru: 2, modelscope: 0, minimax: 3, siliconflow: 1 };
  let timer = 0;
  const $ = (id) => { if (!nodes.has(id)) nodes.set(id, dom.node(id)); return nodes.get(id); };
  $("credentialDialog").showModal = function () { this.open = true; };
  $("credentialDialog").close = function () { this.open = false; closes.push(() => this.events.close?.forEach((fn) => fn())); };
  const metadata = () => ({ services: Object.fromEntries(Object.entries(counts).map(([service, count]) => [service, { configured: count > 0, count }])) });
  const context = {
    $, el: dom.el, icon: dom.icon, AbortController, console,
    // 密钥来源标注要读 /api/status（这台电脑上「生效的」是旧环境变量那份），
    // 所以这两个测试桩也得有 state；这里没有旧环境变量，判定就是「没有说明」。
    state: { status: null }, credentialSourceNote: () => "",
    document: { hidden: false, addEventListener(type, fn) { (events[type] ||= []).push(fn); } },
    window: { addEventListener(type, fn) { (windows[type] ||= []).push(fn); } },
    requestAnimationFrame() { return 0; }, cancelAnimationFrame() {}, anyDialogOpen: () => $("credentialDialog").open,
    setTimeout(fn, delay) { const id = ++timer; timers.set(id, { fn, delay }); return id; }, clearTimeout(id) { timers.delete(id); },
    toast() {}, confirmDialog: async () => true, loadStatus: async () => true,
    fetch: async (url, options) => {
      calls.push({ url, options, body: options.body ? JSON.parse(options.body) : null });
      if (url.endsWith("/key/reveal")) return context.reveal ? context.reveal(calls.at(-1)) :
        response({ ...calls.at(-1).body, key: `synthetic-${calls.at(-1).body.service}-${calls.at(-1).body.index}` });
      if (options.method === "POST") Object.entries(calls.at(-1).body.services).forEach(([service, item]) => {
        if (item.action === "clear") counts[service] = 0;
        if (item.action === "replace") counts[service] = item.accounts.length;
      });
      return response(metadata());
    }
  };
  vm.runInNewContext(apiFunction + functions + listeners + "\nglobalThis.savedRows = credentialSavedRows; globalThis.fields = CREDENTIAL_FIELDS;", context);
  const row = (service, index = 0) => context.savedRows.get(`${service}:${index}`);
  const submit = () => $("credentialForm").events.submit[0]({ preventDefault() {} });
  const expire = (delay) => {
    for (const [id, value] of [...timers]) if (value.delay === delay) { timers.delete(id); value.fn(); }
  };
  return { context, $, calls, counts, metadata, row, submit, timers, expire, events, windows,
    flushClose() { while (closes.length) closes.shift()(); } };
}

function cropCloseScenario({ dirty = false, backendChanged = false } = {}) {
  const nodes = new Map(), frames = [], tasks = [], log = [];
  const $ = (id) => { if (!nodes.has(id)) nodes.set(id, dom.node(id)); return nodes.get(id); };
  $("pageDialog").open = true;
  $("pageDialog").close = function () { log.push("closed"); this.open = false; };
  const q = { id: 7, state: "green", approved: false, todo: backendChanged, green: !backendChanged };
  const frozen = { dirty, wasDirty: dirty, todo: false, green: true, approved: false, ai: false,
    waiting: false, red: false, unpublished: false };
  const state = { paperId: "paper", paper: {}, cropDraftAttention: new Map([[7, frozen]]), rendered: new Map([[7, "cached"]]) };
  const dialog = { active: true, session: 1, question: q, paperId: "paper", mode: "figures", boxes: [], ignoredCandidates: new Set(),
    page: 0, saving: false, closing: false, sketch: null, pendingFigure: null, zoomFrame: 0 };
  const context = { $, state, dialog, pageOpenIntent: 0, CROP_EDIT_KEY: "crop", QBManualCrop: App, editGuard: App.createEditGuard(),
    questionById: () => q, isApproved: () => false, isAiApproved: () => false, needsCheck: () => q.todo, needsGeneralReview: () => q.green,
    cancelFigureSketch() {}, cancelPendingPageOpening() { context.pageOpenIntent++; }, closeFigureSlotMenu() {}, clearPagePanKey() {},
    setCropSaving() {}, showCropGuide() {}, hasRegionReadSubmission: () => false, toast() {},
    counts() { log.push("counts"); return {}; }, renderFilters() {}, renderMeter() {}, renderCards() { log.push("cards"); },
    requestAnimationFrame(fn) { frames.push(fn); return frames.length; }, cancelAnimationFrame() {},
    setTimeout(fn) { tasks.push(fn); }, confirmDialog: async () => { log.push("confirm"); return true; }
  };
  vm.runInNewContext(between("  function cropSnapshot()", "  function syncCropDraftClassification()")
    + between("  function syncCropDraftClassification()", "  function showCropResult("), context);
  context.trackCropDraft();
  if (dirty) dialog.boxes.push({ page_idx: 0, bbox: [1, 2, 30, 40], slot: "A" });
  return { context, $, dialog, state, frames, tasks, log };
}

(async () => {
  const masked = scenario(); await masked.context.openCredentialSettings();
  assert.equal(masked.$("credentialSavedTotal").textContent, "共保存 6 个密钥");
  for (const [service, count] of Object.entries(masked.counts)) {
    const field = masked.context.fields[service];
    assert.equal(masked.$(field.state).textContent, `已保存 ${count} 个`);
    assert.equal(masked.$(field.list).children.length, count || 1);
    assert.equal(masked.$(field.input).value, "");
    for (let index = 0; index < count; index++) assert.equal(masked.row(service, index).value.textContent, "****************");
  }
  await masked.context.toggleCredentialKey("minimax", 1);
  const viewed = masked.row("minimax", 1), call = masked.calls.at(-1);
  assert.deepEqual(copy(call.body), { service: "minimax", index: 1 });
  assert.equal(call.options.method, "POST"); assert.equal(call.options.cache, "no-store");
  assert.equal(call.options.headers["X-QB-Request"], "1");
  assert.equal(viewed.value.textContent, "synthetic-minimax-1");
  assert.equal(viewed.button.getAttribute("aria-pressed"), "true");
  assert.equal(masked.$("credentialMinimaxInput").value, "", "Viewing never populates a replacement field");
  const beforeSave = masked.calls.length; await masked.submit();
  assert.equal(masked.calls.length, beforeSave, "Only viewing a stored value sends no save request");
  await masked.context.toggleCredentialKey("mineru", 0);
  await masked.context.toggleCredentialKey("minimax", 1);
  assert.equal(viewed.value.textContent, "****************");
  assert.equal(masked.row("mineru", 0).visible, true, "Rows have independent reveal controls");
  masked.expire(60000); assert.equal(masked.row("mineru", 0).value.textContent, "****************");
  assert.equal(masked.timers.size, 0);
  await masked.context.toggleCredentialKey("siliconflow", 0);
  masked.$("credentialMinimaxInput").value = "new-synthetic-a;new-synthetic-b";
  await masked.submit();
  const write = masked.calls.findLast((item) => item.options.method === "POST" && item.url === "/api/settings/credentials");
  assert.deepEqual(copy(write.body.services.minimax), { action: "replace", accounts: ["new-synthetic-a", "new-synthetic-b"] });
  for (const service of ["mineru", "modelscope", "siliconflow"]) assert.deepEqual(copy(write.body.services[service]), { action: "keep" });
  assert.equal(JSON.stringify(write.body).includes("synthetic-siliconflow"), false, "A revealed saved key is never resent");
  assert.equal(masked.$("credentialSavedTotal").textContent, "共保存 5 个密钥");
  assert.equal(masked.row("siliconflow", 0).visible, false);

  const cancelled = scenario(); await cancelled.context.openCredentialSettings();
  const wait = deferred(); cancelled.context.reveal = () => wait.promise;
  const task = cancelled.context.toggleCredentialKey("mineru", 0);
  const signal = cancelled.calls.at(-1).options.signal;
  await cancelled.context.toggleCredentialKey("mineru", 0);
  assert.equal(signal.aborted, true);
  wait.resolve(response({ service: "mineru", index: 0, key: "late-synthetic" })); await task;
  assert.equal(cancelled.row("mineru").value.textContent, "****************");
  assert.equal(cancelled.$("credentialResult").textContent, "");

  const reopened = scenario(); await reopened.context.openCredentialSettings();
  const late = deferred(); reopened.context.reveal = () => late.promise;
  const oldTask = reopened.context.toggleCredentialKey("minimax", 0);
  const oldRow = reopened.row("minimax");
  const closeCalls = reopened.calls.length;
  reopened.context.requestCredentialClose();
  assert.equal(reopened.calls.length, closeCalls, "Closing sends no fetch, save or reload request");
  assert.equal(reopened.$("credentialDialog").open, false);
  assert.equal(oldRow.controller, null); assert.equal(reopened.calls.at(-1).options.signal.aborted, true);
  await reopened.context.openCredentialSettings();
  reopened.context.reveal = (request) => response({ ...request.body, key: "new-scope-synthetic" });
  await reopened.context.toggleCredentialKey("minimax", 0);
  reopened.flushClose();
  reopened.$("credentialMinimaxInput").value = "new-session-synthetic";
  late.resolve(response({ service: "minimax", index: 0, key: "old-session-synthetic" })); await oldTask;
  assert.equal(reopened.row("minimax").value.textContent, "new-scope-synthetic", "Queued old close/reveal cannot clear a newly revealed key");
  assert.equal(reopened.$("credentialMinimaxInput").value, "new-session-synthetic");

  const changed = scenario(); await changed.context.openCredentialSettings();
  const stale = deferred(); changed.context.reveal = () => stale.promise;
  const staleTask = changed.context.toggleCredentialKey("minimax", 2);
  const staleSignal = changed.calls.at(-1).options.signal;
  changed.counts.minimax = 0; changed.context.renderCredentialStates(changed.metadata());
  assert.equal(staleSignal.aborted, true);
  stale.resolve(response({ service: "minimax", index: 2, key: "deleted-synthetic" })); await staleTask;
  assert.equal(changed.row("minimax", 2), undefined); assert.equal(changed.$("credentialSavedTotal").textContent, "共保存 3 个密钥");

  for (const result of [{ service: "mineru", index: 0, key: "wrong-provider-synthetic" },
    { service: "minimax", index: 1, key: "wrong-index-synthetic" }, { service: "minimax", index: 0, key: "" }]) {
    const invalid = scenario(); await invalid.context.openCredentialSettings(); invalid.context.reveal = () => response(result);
    await invalid.context.toggleCredentialKey("minimax", 0);
    assert.equal(invalid.row("minimax").value.textContent, "****************");
    assert.match(invalid.$("credentialResult").textContent, /暂时无法查看/);
  }
  const error = scenario(); await error.context.openCredentialSettings();
  error.context.reveal = () => response({ error: "synthetic-sensitive-error-value" }, false);
  await error.context.toggleCredentialKey("mineru", 0);
  assert.equal(error.$("credentialResult").textContent.includes("synthetic-sensitive"), false);
  const timed = scenario(); await timed.context.openCredentialSettings(); const timeout = deferred();
  timed.context.reveal = () => timeout.promise; const timedTask = timed.context.toggleCredentialKey("siliconflow", 0);
  const timedSignal = timed.calls.at(-1).options.signal; timed.expire(15000); assert.equal(timedSignal.aborted, true);
  timeout.resolve(response({ service: "siliconflow", index: 0, key: "timeout-synthetic" })); await timedTask;
  assert.equal(timed.row("siliconflow").value.textContent, "****************"); assert.equal(timed.timers.size, 0);

  const hidden = scenario(); await hidden.context.openCredentialSettings(); await hidden.context.toggleCredentialKey("mineru", 0);
  hidden.context.document.hidden = true; hidden.events.visibilitychange.forEach((fn) => fn());
  assert.equal(hidden.row("mineru").visible, false);
  await hidden.context.toggleCredentialKey("minimax", 0); hidden.windows.pagehide.forEach((fn) => fn());
  assert.equal(hidden.row("minimax").visible, false);
  await hidden.context.toggleCredentialKey("siliconflow", 0); hidden.context.requestCredentialClose();
  assert.equal(hidden.row("siliconflow").value.textContent, "****************", "Close conceals immediately, before native close events");
  assert.equal(hidden.timers.size, 0);

  const clean = cropCloseScenario(); const close = clean.context.requestPageDialogClose();
  assert.equal(clean.$("pageDialog").open, false); assert.deepEqual(clean.log, ["closed"]);
  assert.equal(clean.frames.length, 0); assert.equal(clean.state.rendered.get(7), "cached"); await close;
  const refreshing = cropCloseScenario({ backendChanged: true }); await refreshing.context.requestPageDialogClose();
  assert.deepEqual(refreshing.log, ["closed"]); assert.equal(refreshing.frames.length, 1);
  refreshing.frames.shift()(); assert.deepEqual(refreshing.log, ["closed"], "The animation frame only queues cleanup after paint");
  refreshing.tasks.shift()(); assert.deepEqual(refreshing.log, ["closed", "counts", "cards"]);
  const newScope = cropCloseScenario({ backendChanged: true }); await newScope.context.requestPageDialogClose();
  newScope.frames.shift()(); newScope.dialog.active = true; newScope.dialog.session++; newScope.$("pageDialog").open = true;
  newScope.tasks.shift()(); assert.deepEqual(newScope.log, ["closed"], "Old deferred cleanup cannot touch a reopened editor");
  const draft = cropCloseScenario({ dirty: true }); draft.context.confirmDialog = async () => false;
  assert.equal(await draft.context.requestPageDialogClose(), false); assert.equal(draft.$("pageDialog").open, true);
  assert.equal(draft.dialog.boxes.length, 1); assert.equal(draft.frames.length, 0);
  draft.context.confirmDialog = async () => true; assert.equal(await draft.context.requestPageDialogClose(), true);
  assert.equal(draft.$("pageDialog").open, false); assert.equal(draft.context.editGuard.hasPendingWork(), false);
  console.log("Saved credentials: masked multi-key counts; explicit no-store reveal; 60s hide; independent rows; keep/replace isolation; close/abort/timeout/stale/provider guards; paint-first clean and dirty crop exit: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
