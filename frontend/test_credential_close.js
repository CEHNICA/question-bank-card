"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const dom = require("./credential-test-dom.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const functions = js.slice(js.indexOf("  const CREDENTIAL_FIELDS = {"), js.indexOf("  function renderSettingsTask("));
const listenersStart = js.indexOf('  Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {', js.indexOf('  $("selectionReread").addEventListener'));
const listeners = js.slice(listenersStart, js.indexOf("  // Queue the values captured", listenersStart));
const dismissalsStart = js.indexOf("  function requestDialogClose(modal)");
const dismissals = js.slice(dismissalsStart, js.indexOf("  setLens(state.lens);", dismissalsStart));
assert.ok(functions && listenersStart > 0 && dismissalsStart > 0);

const providers = ["mineru", "modelscope", "minimax", "siliconflow"];
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const turns = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };

function scenario() {
  const nodes = new Map(), requests = [], messages = [], focuses = [];
  const nativeTasks = [], frames = new Map();
  let frameId = 0, pageCloseCount = 0;
  const saved = Object.fromEntries(providers.map((provider) => [provider, { configured: true, count: 1 }]));
  const closeButtons = [];
  function $(id) {
    if (!nodes.has(id)) nodes.set(id, { ...dom.node(id),
      addEventListener(type, fn) { (this.events[type] ||= []).push(fn); },
      getBoundingClientRect() { return { left: 100, top: 50, right: 500, bottom: 450, width: 400, height: 400 }; },
      querySelectorAll() { return closeButtons; },
      showModal() { assert.equal(this.open, false); this.open = true; },
      close() { if (!this.open) return; this.open = false; nativeTasks.push(() => this.events.close?.forEach((fn) => fn({ target: this }))); },
      focus(options) { focuses.push({ id, options }); }
    });
    return nodes.get(id);
  }
  for (const label of ["关闭", "取消"]) closeButtons.push({ label, disabled: false, events: {},
    addEventListener(type, fn) { (this.events[type] ||= []).push(fn); }, closest() { return $("credentialDialog"); }
  });
  const dialogs = [$("credentialDialog"), $("pageDialog"), $("keysDialog")];
  const context = vm.createContext({ $, console, el: dom.el, icon: dom.icon, AbortController, setTimeout, clearTimeout,
    window: { addEventListener() {} },
    requestAnimationFrame(fn) { const id = ++frameId; frames.set(id, fn); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
    anyDialogOpen() { return dialogs.some((dialog) => dialog.open); },
    document: { addEventListener() {}, querySelectorAll(selector) { return selector === "dialog [data-close]" ? closeButtons : dialogs; } },
    toast(message, kind) { messages.push({ message, kind }); },
    confirmDialog: async () => true,
    requestPageDialogClose() { pageCloseCount++; },
    loadStatus: async () => true,
    api: async (url, options) => {
      requests.push({ url, ...(options || {}) });
      if (options?.body?.services) Object.entries(options.body.services).forEach(([provider, operation]) => {
        if (operation.action === "clear") saved[provider] = { configured: false, count: 0 };
        else if (operation.action === "replace") saved[provider] = { configured: true, count: operation.accounts.length };
      });
      return { services: JSON.parse(JSON.stringify(saved)) };
    }
  });
  vm.runInContext(functions + listeners + dismissals + "\nglobalThis.fields = CREDENTIAL_FIELDS;", context);
  const drainNative = () => { while (nativeTasks.length) nativeTasks.shift()(); };
  const drainFrames = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach((fn) => fn()); };
  const clickClose = (index = 0) => closeButtons[index].events.click.forEach((fn) => fn());
  const backdrop = () => {
    const modal = $("credentialDialog");
    const event = { target: modal, button: 0, clientX: 20, clientY: 20 };
    modal.events.pointerdown.forEach((fn) => fn(event));
    modal.events.click.forEach((fn) => fn(event));
  };
  const escape = () => {
    let prevented = false;
    $("credentialDialog").events.cancel.forEach((fn) => fn({ preventDefault() { prevented = true; } }));
    if (!prevented) $("credentialDialog").close();
    return prevented;
  };
  const submit = () => $("credentialForm").events.submit[0]({ preventDefault() {} });
  const values = () => providers.map((provider) => $(context.fields[provider].input).value);
  return { context, $, requests, messages, focuses, saved, closeButtons, nativeTasks, drainNative, drainFrames, clickClose, backdrop, escape, submit, values,
    pageCloseCount: () => pageCloseCount };
}

(async () => {
  // Closing during read-only status loading is immediate and never saves a draft.
  const loading = scenario(), status = deferred();
  loading.context.api = (url, options) => { loading.requests.push({ url, options }); return status.promise; };
  const opened = loading.context.openCredentialSettings();
  loading.clickClose();
  assert.equal(loading.$("credentialDialog").open, false);
  loading.drainNative(); loading.drainFrames();
  assert.ok(loading.values().every((value) => value === ""));
  assert.equal(loading.focuses.at(-1).id, "settingsCredentialOpen");
  status.resolve({ services: loading.saved }); await opened; loading.drainFrames();
  assert.equal(loading.focuses.filter((focus) => focus.id === "credentialMineruInput").length, 0);
  assert.equal(loading.requests.length, 1);
  assert.equal(loading.requests[0].options, undefined, "Close never posts, deletes or saves a secret");

  for (const dismiss of ["cancel", "escape", "backdrop"]) {
    const closing = scenario();
    await closing.context.openCredentialSettings(); closing.drainFrames();
    if (dismiss === "cancel") closing.clickClose(1);
    else if (dismiss === "escape") assert.equal(closing.escape(), false);
    else closing.backdrop();
    assert.equal(closing.$("credentialDialog").open, false);
    closing.drainNative(); closing.drainFrames();
    assert.ok(closing.values().every((value) => value === ""));
    await closing.context.openCredentialSettings();
    await closing.context.openCredentialSettings();
    closing.drainFrames();
    assert.equal(closing.requests.length, 2, "Duplicate open does not create overlapping loads");
    assert.ok(closing.values().every((value) => value === ""));
    assert.ok(Object.values(closing.saved).every((item) => item.configured));
  }

  const retargeted = scenario();
  await retargeted.context.openCredentialSettings();
  retargeted.$("credentialMinimaxInput").value = "internal-draft-test-only";
  retargeted.$("credentialDialog").events.pointerdown.forEach((fn) => fn({ target: retargeted.$("credentialMinimaxInput"), button: 0, clientX: 200, clientY: 150 }));
  // The internal pointer target can be rebuilt before the browser dispatches
  // click. A retargeted dialog click must not count as a backdrop click.
  retargeted.$("credentialDialog").events.click.forEach((fn) => fn({ target: retargeted.$("credentialDialog"), clientX: 200, clientY: 150 }));
  assert.equal(retargeted.$("credentialDialog").open, true);
  assert.equal(retargeted.$("credentialMinimaxInput").value, "internal-draft-test-only");
  retargeted.$("credentialDialog").events.pointerdown.forEach((fn) => fn({ target: retargeted.$("credentialDialog"), button: 0, clientX: 20, clientY: 20 }));
  retargeted.$("credentialDialog").events.click.forEach((fn) => fn({ target: retargeted.$("credentialDialog"), clientX: 200, clientY: 150 }));
  assert.equal(retargeted.$("credentialDialog").open, true, "An outside press released inside must retain the credential dialog and its draft");
  assert.equal(retargeted.$("credentialMinimaxInput").value, "internal-draft-test-only");
  retargeted.backdrop();
  await turns();
  assert.equal(retargeted.$("credentialDialog").open, false, "A genuine outside click still uses the secret draft close guard");

  // Native close is asynchronous: old close events and status replies cannot
  // clear the second session's draft or take its focus.
  const reopened = scenario(), firstStatus = deferred(), secondStatus = deferred();
  let reads = 0;
  reopened.context.api = () => (++reads === 1 ? firstStatus : secondStatus).promise;
  const firstOpen = reopened.context.openCredentialSettings();
  reopened.clickClose();
  const secondOpen = reopened.context.openCredentialSettings();
  reopened.$("credentialMinimaxInput").value = "second-session-test-only";
  reopened.drainNative(); reopened.drainFrames();
  assert.equal(reopened.$("credentialMinimaxInput").value, "second-session-test-only");
  firstStatus.reject(Error("Old status failure")); await firstOpen;
  reopened.drainFrames();
  assert.equal(reopened.messages.length, 0);
  assert.equal(reopened.focuses.length, 0);
  assert.equal(reopened.$("credentialResult").textContent, "");
  secondStatus.resolve({ services: reopened.saved }); await secondOpen; reopened.drainFrames();
  assert.equal(reopened.focuses.length, 1);
  assert.equal(reopened.focuses[0].id, "credentialMineruInput");
  assert.equal(reopened.focuses[0].options.preventScroll, true);

  // A closed dialog's delayed focus callback must leave a newer dialog alone.
  const otherDialog = scenario();
  await otherDialog.context.openCredentialSettings();
  otherDialog.clickClose(); otherDialog.drainNative();
  otherDialog.$("keysDialog").showModal(); otherDialog.drainFrames();
  assert.equal(otherDialog.focuses.length, 0);
  otherDialog.context.requestDialogClose(otherDialog.$("keysDialog"));
  assert.equal(otherDialog.$("keysDialog").open, false);
  otherDialog.$("pageDialog").showModal();
  otherDialog.context.requestDialogClose(otherDialog.$("pageDialog"));
  assert.equal(otherDialog.pageCloseCount(), 1, "Generic close still uses crop draft protection");

  // Reproduce the actual lock: a successful local key mutation followed by a
  // status request that has not returned. Close must already be enabled.
  for (const operation of ["save", "delete"]) {
    const refreshed = scenario(), delayedRefresh = deferred();
    await refreshed.context.openCredentialSettings(); refreshed.drainFrames();
    refreshed.context.loadStatus = () => delayedRefresh.promise;
    if (operation === "save") refreshed.$("credentialMinimaxInput").value = "replacement-test-only";
    const task = operation === "save" ? refreshed.submit() : refreshed.context.deleteCredential("mineru");
    await turns();
    assert.ok(refreshed.closeButtons.every((button) => !button.disabled));
    assert.equal(refreshed.context.requestCredentialClose(), true);
    refreshed.drainNative(); refreshed.drainFrames();
    await refreshed.context.openCredentialSettings();
    refreshed.$("credentialMinimaxInput").value = "new-session-test-only";
    delayedRefresh.resolve(false); await task;
    assert.equal(refreshed.$("credentialResult").textContent, "", "Old refresh failure cannot label a new session");
    assert.equal(refreshed.$("credentialMinimaxInput").value, "new-session-test-only");
  }

  const overlapping = scenario(), oldRefresh = deferred(), nextMutation = deferred();
  await overlapping.context.openCredentialSettings();
  overlapping.context.loadStatus = () => oldRefresh.promise;
  overlapping.$("credentialMinimaxInput").value = "first-test-only";
  const firstSave = overlapping.submit(); await turns();
  overlapping.context.api = () => nextMutation.promise;
  overlapping.$("credentialMinimaxInput").value = "second-test-only";
  const secondSave = overlapping.submit(); await turns();
  oldRefresh.resolve(true); await firstSave;
  assert.ok(overlapping.closeButtons.every((button) => button.disabled), "An old refresh completion cannot unlock a newer mutation");
  assert.equal(overlapping.context.requestCredentialClose(), false);
  nextMutation.resolve({ services: overlapping.saved }); await secondSave;
  assert.ok(overlapping.closeButtons.every((button) => !button.disabled));

  // Only an actual pending mutation/confirmation locks dismissal. Every entry
  // point honours that guard, including disabled buttons triggered in code.
  const busy = scenario(), confirmation = deferred();
  await busy.context.openCredentialSettings();
  busy.context.confirmDialog = () => confirmation.promise;
  const deleting = busy.context.deleteCredential("mineru");
  busy.clickClose();
  assert.equal(busy.$("credentialDialog").open, true);
  assert.equal(busy.context.requestCredentialClose(), false);
  assert.equal(busy.escape(), true);
  confirmation.resolve(false); await deleting;
  assert.equal(busy.context.requestCredentialClose(), true);
  busy.drainNative();
  assert.equal(busy.requests.length, 1, "Cancelled deletion performs no mutation");

  console.log("Credential dismissal: Close/Cancel/Esc/backdrop, draft clearing, queued close/reopen, stale focus/error, delayed refresh unlock, mutation guard: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
