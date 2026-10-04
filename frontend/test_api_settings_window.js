"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const dom = require("./credential-test-dom.js");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const source = js.slice(js.indexOf("  const CREDENTIAL_FIELDS = {"), js.indexOf("  function renderSettingsTask("));
const listenerStart = js.indexOf('  Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {', js.indexOf('  $("questionTrash").addEventListener("click", openQuestionTrash);'));
const listeners = js.slice(listenerStart, js.indexOf("  // Queue the values captured", listenerStart));
const leaveSource = js.slice(js.indexOf("  async function prepareSettingsLeave()"), js.indexOf("  async function saveModelSettingsNow("));
const routeSource = js.slice(js.indexOf("  const SETTINGS_HASHES = {"), js.indexOf("  function renderSettingsReady()"));
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const turns = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };

// Check the actual form ancestry, not just matching the presence of new IDs.
const stack = [], ancestry = new Map(), counts = new Map();
const voids = new Set(["meta", "link", "img", "input", "br", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"]);
for (const match of html.matchAll(/<\/?([a-z][a-z0-9-]*)([^>]*)>/gi)) {
  const tag = match[1].toLowerCase(), end = match[0].startsWith("</");
  if (end) { assert.equal(stack.pop()?.tag, tag, `Balanced HTML at ${match[0]}`); continue; }
  const id = match[2].match(/\bid="([^"]+)"/)?.[1];
  if (id) { counts.set(id, (counts.get(id) || 0) + 1); ancestry.set(id, [...stack]); }
  if (tag === "form") assert.ok(!stack.some((item) => item.tag === "form"), "A form cannot be nested inside another form");
  if (!voids.has(tag) && !match[0].endsWith("/>")) stack.push({ tag, id });
}
assert.equal(stack.length, 0);
for (const id of ["credentialDialog", "credentialForm", "modelSettingsForm", "credentialReadingPanel", "credentialAnswerPanel", "libraryAIAPISettingsMount"]) assert.equal(counts.get(id), 1, `${id} is unique`);
for (const id of ["credentialForm", "modelSettingsForm"]) {
  assert.ok(ancestry.get(id).some((item) => item.id === "credentialReadingPanel"));
  assert.ok(!ancestry.get(id).some((item) => item.id === "credentialForm"));
}
assert.ok(ancestry.get("libraryAIAPISettingsMount").some((item) => item.id === "credentialAnswerPanel"));
assert.ok(!ancestry.get("libraryAIAPISettingsMount").some((item) => item.tag === "form"));
assert.doesNotMatch(html, /libraryAISettingsMount/);
assert.match(js, /modelFormDirty \|\| credentialHasNewKeys\(\) \|\| window\.LibraryAISettings\?\.hasUnsavedChanges/);

function scenario() {
  const nodes = new Map(), calls = [], nativeTasks = [], confirmations = [], frames = new Map();
  const windowEvents = new Map();
  let frame = 0, confirmResult = true;
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, { ...dom.node(id),
      showModal() { assert.equal(this.open, false, "Only one native configuration dialog opens"); this.open = true; calls.push("show"); },
      close() { this.open = false; nativeTasks.push(() => this.events.close?.forEach((fn) => fn())); }
    });
    return nodes.get(id);
  };
  const tabs = [...html.matchAll(/data-settings-tab="([^"]+)"/g)].map((match) => { const node = $(match[1] + "Tab"); node.dataset = { settingsTab: match[1] }; return node; });
  const pages = [...html.matchAll(/id="([^"]+)" class="settings-page" role="tabpanel"/g)].map((match) => $(match[1]));
  const ai = { active: false, dirty: false, mutating: false, metadata: false, key: "", visible: false, mountCount: 0, activateCount: 0, deactivateCount: 0,
    async mount(host, opts) { assert.equal(host.id, "libraryAIAPISettingsMount"); assert.deepEqual(JSON.parse(JSON.stringify(opts)), { embedded: true }); this.mountCount++; await this.activate(); },
    async activate() { if (this.active) return; this.active = true; this.activateCount++; if (this.load) { this.metadata = true; await this.load; this.metadata = false; } },
    hideSecrets() { this.visible = false; calls.push("hide-answer"); },
    deactivate() { if (this.mutating || this.dirty) return false; this.active = false; this.deactivateCount++; this.metadata = false; this.key = ""; this.visible = false; return true; },
    hasUnsavedChanges() { return this.dirty; }, isBusy() { return this.metadata || this.mutating; }, isMutating() { return this.mutating; },
    discard() { if (this.isBusy()) return false; this.dirty = false; this.key = ""; calls.push("discard-answer"); return true; }
  };
  const context = vm.createContext({ $, el: dom.el, icon: dom.icon, console, AbortController, setTimeout, clearTimeout,
    window: { LibraryAISettings: ai, addEventListener(name, fn) { windowEvents.set(name, fn); }, location: { pathname: "/settings", hash: "", search: "" } },
    document: { addEventListener() {}, querySelectorAll(selector) { if (selector === "[data-settings-tab]") return tabs; if (selector === "#settingsDialog .settings-page") return pages; throw new Error(selector); }, querySelector() { return $("scroll"); } },
    requestAnimationFrame(fn) { frames.set(++frame, fn); return frame; }, cancelAnimationFrame(id) { frames.delete(id); },
    anyDialogOpen: () => $("credentialDialog").open, toast: () => {}, loadStatus: async () => true,
    confirmDialog: async (options) => { confirmations.push(options); return typeof confirmResult === "boolean" ? confirmResult : confirmResult.promise; },
    api: async (url, options) => { calls.push({ url, options }); return { services: { minimax: { configured: true, count: 2 } } }; }
  });
  vm.runInContext(source + listeners + routeSource + "\nlet modelFormDirty = false, lastModelSave = null, modelSaving = Promise.resolve();\n" + leaveSource, context);
  return { $, ai, context, calls, nativeTasks, confirmations, windowEvents,
    setConfirmation(value) { confirmResult = value; },
    drainClose() { while (nativeTasks.length) nativeTasks.shift()(); },
    setModel(guard) { context.guard = guard; vm.runInContext("credentialModelGuard = guard", context); },
    escape() { let prevented = false; $("credentialDialog").events.cancel[0]({ preventDefault() { prevented = true; } }); if (!prevented) $("credentialDialog").close(); return prevented; }
  };
}

(async () => {
  for (const oldHash of ["#ai", "#api"]) {
    const legacy = scenario(); legacy.context.window.location.hash = oldHash;
    legacy.context.syncSettingsRoute(); await turns();
    assert.equal(legacy.$("credentialDialog").open, true); assert.equal(legacy.$("credentialAnswerPanel").hidden, false);
    assert.equal(legacy.$("credentialReadingPanel").hidden, true); assert.equal(legacy.ai.mountCount, 1);
    legacy.ai.key = "legacy-route-synthetic-draft"; legacy.ai.dirty = true;
    legacy.$("credentialMinimaxInput").value = "reading-route-synthetic-draft";
    legacy.context.window.location.hash = oldHash === "#ai" ? "#api" : "#ai";
    legacy.windowEvents.get("hashchange")(); await turns();
    assert.equal(legacy.calls.filter((call) => call === "show").length, 1, "Legacy aliases reuse the existing unified dialog");
    assert.equal(legacy.ai.mountCount, 1); assert.equal(legacy.ai.key, "legacy-route-synthetic-draft");
    assert.equal(legacy.$("credentialMinimaxInput").value, "reading-route-synthetic-draft"); assert.equal(legacy.confirmations.length, 0);
  }
  const switched = scenario();
  await switched.context.window.APISettings.open("reading");
  switched.$("credentialMinimaxInput").value = "synthetic-unsaved";
  await switched.context.window.APISettings.open("answers");
  switched.ai.key = "synthetic-answer-draft"; switched.ai.dirty = true; switched.ai.visible = true;
  await switched.context.window.APISettings.open("reading");
  assert.equal(switched.ai.key, "synthetic-answer-draft"); assert.equal(switched.ai.dirty, true); assert.equal(switched.ai.visible, false);
  assert.equal(switched.$("credentialMinimaxInput").value, "synthetic-unsaved");
  await switched.context.window.APISettings.open("answers");
  assert.equal(switched.ai.mountCount, 1); assert.equal(switched.ai.activateCount, 1);
  assert.equal(switched.calls.filter((call) => call === "show").length, 1);
  assert.equal(switched.calls.filter((call) => call.url === "/api/settings/credentials").length, 1, "Repeated opens retain the same metadata session");
  switched.setConfirmation(false);
  switched.context.requestCredentialClose(); await turns();
  assert.equal(switched.$("credentialDialog").open, true); assert.equal(switched.confirmations.length, 1);
  assert.equal(switched.$("credentialMinimaxInput").value, "synthetic-unsaved"); assert.equal(switched.ai.key, "synthetic-answer-draft");
  switched.setConfirmation(true); assert.equal(switched.escape(), true); await turns();
  assert.equal(switched.$("credentialDialog").open, false); assert.equal(switched.confirmations.length, 2);
  assert.equal(switched.confirmations[1].title, "API 配置还没保存");
  assert.equal(switched.$("credentialMinimaxInput").value, ""); assert.equal(switched.ai.key, "");
  assert.equal(switched.calls.filter((call) => call === "discard-answer").length, 1, "Both genuine drafts use one accepted confirmation");

  const clean = scenario(); await clean.context.openCredentialSettings("answers");
  clean.$("credentialMinimaxInput").value = " \n "; clean.ai.visible = true;
  const before = clean.calls.length;
  assert.equal(clean.context.requestCredentialClose(), true); assert.equal(clean.$("credentialDialog").open, false);
  assert.equal(clean.confirmations.length, 0); assert.equal(clean.ai.visible, false);
  assert.equal(clean.calls.slice(before).filter((call) => call.url).length, 0, "Clean close performs no reload, render or save");

  const loading = scenario(), wait = deferred(); loading.ai.load = wait.promise;
  const open = loading.context.openCredentialSettings("answers"); await turns();
  assert.equal(loading.ai.isBusy(), true); assert.equal(loading.context.requestCredentialClose(), true);
  assert.equal(loading.ai.active, false, "Read-only metadata never locks close");
  wait.resolve(); await open;

  for (const group of ["answers", "reading", "model"]) {
    const busy = scenario(); await busy.context.openCredentialSettings("answers");
    if (group === "answers") busy.ai.mutating = true;
    if (group === "reading") vm.runInContext("credentialBusy = true", busy.context);
    if (group === "model") busy.setModel({ isMutating: () => true, hasUnsavedChanges: () => true });
    assert.equal(busy.context.requestCredentialClose(), false); assert.equal(busy.$("credentialDialog").open, true); assert.equal(busy.confirmations.length, 0);
  }
  const model = scenario(); await model.context.openCredentialSettings();
  let dirty = true, succeeds = false, saves = 0;
  model.setModel({ isMutating: () => false, hasUnsavedChanges: () => dirty, async prepareClose() { saves++; if (succeeds) dirty = false; return succeeds; } });
  model.context.requestCredentialClose(); await turns();
  assert.equal(model.$("credentialDialog").open, true, "Failed model autosave retains the form");
  succeeds = true; model.context.requestCredentialClose(); await turns();
  assert.equal(model.$("credentialDialog").open, false); assert.equal(saves, 2);

  const reopened = scenario(), decision = deferred(); await reopened.context.openCredentialSettings("answers");
  reopened.$("credentialMinimaxInput").value = "old-synthetic"; reopened.ai.dirty = true;
  reopened.setConfirmation(decision); reopened.context.requestCredentialClose(); await turns();
  // Simulate a native navigation/controller close while a confirmation is pending.
  reopened.ai.dirty = false; reopened.$("credentialDialog").close(); reopened.drainClose();
  await reopened.context.openCredentialSettings("answers"); reopened.$("credentialMinimaxInput").value = "new-synthetic"; reopened.ai.key = "new-answer-synthetic"; reopened.ai.dirty = true;
  decision.resolve(true); await turns();
  assert.equal(reopened.$("credentialDialog").open, true); assert.equal(reopened.$("credentialMinimaxInput").value, "new-synthetic"); assert.equal(reopened.ai.key, "new-answer-synthetic");
  assert.equal(reopened.calls.filter((call) => call === "discard-answer").length, 0, "Late confirmation cannot discard a new session");

  const navigation = scenario(); await navigation.context.openCredentialSettings("answers");
  navigation.$("credentialMineruInput").value = "navigation-synthetic"; navigation.ai.dirty = true;
  navigation.setConfirmation(false);
  assert.equal(await navigation.context.prepareSettingsLeave(), false, "Existing normal navigation respects both API drafts");
  assert.equal(navigation.$("credentialMineruInput").value, "navigation-synthetic"); assert.equal(navigation.ai.dirty, true);
  navigation.setConfirmation(true);
  assert.equal(await navigation.context.prepareSettingsLeave(), true, "Confirmed navigation closes and releases the one configuration window");
  assert.equal(navigation.confirmations.length, 2);
  console.log("Unified API settings: unique sibling forms, embedded mount, retained cross-tab drafts, one real-change confirmation, clean close, metadata/mutation guards, model autosave and stale session protection: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
