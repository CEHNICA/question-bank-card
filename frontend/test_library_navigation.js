"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const state = { basket: ["pub-1"], draft: null, draftDirty: false, draftBaseline: null, catalog: new Map(), features: {} };
  const printState = { token: 0, solutions: {}, solutionRecords: new Map(), exporting: false, items: [], missing: [] };
  const controls = new Map(), confirms = [], notices = [], navigations = [], unloads = [], requests = [];
  const control = id => { if (!controls.has(id)) controls.set(id, { textContent: "", className: "", value: "", disabled: false, hidden: false, focus() {} }); return controls.get(id); };
  const ui = { printTitle: { value: "练习" }, basketCount: {}, basketButton: { classList: { remove() {}, add() {} } }, printAiBox: {}, paper: { replaceChildren() {} }, sheet: { open: false, hidden: true, showModal() { this.open = true; } } };
  let answer = true, assignmentFails = false, saveResponse;
  const options = { document: "questions", font_size: 12, option_layout: "auto" };
  const context = { state, printState, ui, printNames: new Map(), $: control,
    currentPrintOptions: () => ({ ...options }), solutions: require("./library-solutions.js"),
    confirmDialog: details => { confirms.push(details); return typeof answer === "function" ? answer(details) : Promise.resolve(answer); },
    toast: (...value) => notices.push(value), syncExportButtons() {}, node() {},
    prunePrintChoices() {}, renderBasket() {}, syncSelection() {}, refreshBasket() {},
    batchItems: async ids => ({ items: ids.map(id => ({ id, number: 1 })), missing: [] }),
    resolvePrintSolutions: async items => { for (const item of items) printState.solutions[item.id] ||= "origin"; },
    renderPrint: items => { printState.items = items; printState.layoutPromise = Promise.resolve(); },
    fetch: async (url, opts) => { requests.push({ url, opts }); return saveResponse ? saveResponse() : { ok: true, json: async () => ({ draft: { id: "saved", title: ui.printTitle.value, ids: [...state.basket] } }) }; },
    document: { activeElement: null, body: { classList: { add() {} } } }, URL,
    window: { localStorage: { setItem() {} }, matchMedia: () => ({ matches: false }), location: { href: "http://127.0.0.1:8768/library?document=paper", assign(url) {
      if (assignmentFails) throw Error("navigation blocked");
      navigations.push(url);
      const event = { prevented: false, preventDefault() { this.prevented = true; } };
      context.protectLibraryBeforeUnload(event); unloads.push(event);
    } } }
  };
  vm.createContext(context);
  vm.runInContext(section("  function saveBasket()", "  function syncUrl()"), context);
  vm.runInContext(section("  async function openPrint()", "  async function saveDraft("), context);
  vm.runInContext(section("  async function saveDraft(", "  async function openDrafts()"), context);
  vm.runInContext(section("  let libraryNavigationPending", "  // ---------------------------------------------------------------- 事件"), context);
  const link = (href = "/", changes = {}) => ({ href: new URL(href, context.window.location.href).href, target: "", hasAttribute: () => false,
    closest: selector => selector === ".topnav" ? {} : null, getAttribute: () => null, ...changes });
  const event = anchor => ({ target: { closest: () => anchor }, button: 0, prevented: false, defaultPrevented: false,
    preventDefault() { this.prevented = this.defaultPrevented = true; } });
  const click = async (anchor = link(), changes = {}) => {
    const value = Object.assign(event(anchor), changes); context.handleLibraryNavigation(value);
    if (!value.prevented && !value.ctrlKey && !value.metaKey && !value.shiftKey && !value.altKey && value.button === 0) context.window.location.assign(anchor.href);
    await tick(); return value;
  };
  const unload = () => { const value = { prevented: false, preventDefault() { this.prevented = true; } }; context.protectLibraryBeforeUnload(value); return value; };
  return { context, state, printState, ui, options, control, confirms, notices, navigations, unloads, requests, click, link, event, unload,
    answer(value) { answer = value; }, failAssign(value) { assignmentFails = value; }, saveResponse(value) { saveResponse = value; } };
}

(async () => {
  const h = harness();
  assert.equal(h.unload().prevented, false, "Initial page and persisted basket alone have no unsaved paper");
  await h.context.openPrint();
  assert.equal(h.state.draftDirty, false);
  assert.equal(h.unload().prevented, false, "Opening preview and resolving an original answer is initialization");
  await h.click();
  assert.equal(h.confirms.length, 0); assert.equal(h.unloads.at(-1).prevented, false, "Clean intake navigation has no browser leave warning");
  h.state.basket.push("pub-2"); h.context.saveBasket();
  assert.equal(h.unload().prevented, false, "Merely changing the automatically persisted basket does not create phantom draft edits");

  h.options.font_size = 16; h.context.markDraftDirty(); assert.equal(h.state.draftDirty, true);
  assert.equal(h.unload().prevented, true, "Closing/refreshing a genuinely edited paper remains protected");
  h.options.font_size = 12; h.context.markDraftDirty();
  assert.equal(h.state.draftDirty, false, "Returning to original options removes the stale dirty flag");
  h.ui.printTitle.value = "临时名称"; h.context.markDraftDirty(); assert(h.state.draftDirty);
  h.ui.printTitle.value = "练习"; h.context.markDraftDirty(); assert.equal(h.unload().prevented, false);

  h.options.option_layout = "vertical"; h.context.markDraftDirty();
  const snapshot = h.state.draftBaseline; h.answer(false);
  const cancel = await h.click(); assert(cancel.prevented); assert.equal(h.navigations.length, 1);
  assert.equal(h.confirms.at(-1).title, "组卷还没保存"); assert.equal(h.confirms.at(-1).focusCancel, true);
  assert.equal(h.options.option_layout, "vertical"); assert.equal(h.state.draftBaseline, snapshot); assert(h.state.draftDirty);
  h.answer(true); await h.click();
  assert.equal(h.navigations.length, 2); assert.equal(h.unloads.at(-1).prevented, false, "Confirmed in-app leave must not ask a second native question");
  assert.equal(h.state.draftDirty, true, "Navigation permission must not erase an editor's unsaved state");
  assert.equal(h.unload().prevented, true, "Leave approval is consumed once, not a permanent close bypass");

  h.state.basket = []; h.context.saveBasket();
  assert.equal(h.state.draftDirty, false); assert.equal(h.state.draftBaseline, null);
  assert.equal(h.unload().prevented, false, "Clearing an unassociated basket removes its obsolete paper warning");
  h.state.draft = { id: "draft", title: "既有草稿" }; h.state.draftBaseline = JSON.stringify({ title: "练习", ids: ["pub-1"], print_options: { ...h.options }, solutions: { "pub-1": "origin" } });
  h.context.markDraftDirty(); assert(h.state.draftDirty); assert(h.unload().prevented, "Removing all questions from a saved draft remains a real edit");

  const busy = harness(); await busy.context.openPrint();
  for (const [owner, key] of [[busy.state, "draftSaving"], [busy.printState, "exporting"]]) {
    owner[key] = true; await busy.click(); assert.equal(busy.navigations.length, 0); assert.equal(busy.confirms.length, 0); assert(busy.unload().prevented);
    owner[key] = false;
  }

  const repeated = harness(); await repeated.context.openPrint(); repeated.ui.printTitle.value = "未存卷"; repeated.context.markDraftDirty();
  let finish; repeated.answer(() => new Promise(resolve => { finish = resolve; }));
  const first = repeated.click(); await tick(); await repeated.click();
  assert.equal(repeated.confirms.length, 1, "Repeated links cannot share or bypass the pending confirmation");
  finish(false); await first; await tick(); assert.equal(repeated.navigations.length, 0);
  repeated.answer(() => new Promise(resolve => { finish = resolve; }));
  const changed = repeated.click(); await tick(); repeated.ui.printTitle.value = "后台更新的卷"; repeated.context.markDraftDirty(); finish(true); await changed; await tick();
  assert.equal(repeated.navigations.length, 0, "An approved stale snapshot cannot discard a newer paper edit");
  assert(repeated.notices.at(-1)[0].includes("又有新改动"));

  const saved = harness(); await saved.context.openPrint(); saved.options.font_size = 16; saved.context.markDraftDirty();
  await saved.context.saveDraft(); assert.equal(saved.state.draftDirty, false); assert.equal(saved.unload().prevented, false);
  saved.options.font_size = 12; saved.context.markDraftDirty(); assert(saved.state.draftDirty);
  saved.options.font_size = 16; saved.context.markDraftDirty(); assert.equal(saved.state.draftDirty, false, "Successful save establishes the actual new baseline");
  saved.context.saveBasket(); assert.equal(saved.unload().prevented, false, "Refreshing a saved basket with identical selection stays clean");
  const savedBaseline = saved.state.draftBaseline;
  saved.options.font_size = 14; saved.context.markDraftDirty();
  saved.saveResponse(async () => ({ ok: false, json: async () => ({ error: "写入失败" }) })); await saved.context.saveDraft();
  assert.equal(saved.state.draftBaseline, savedBaseline); assert(saved.state.draftDirty); assert(saved.unload().prevented, "A failed save cannot remove dirty protection");
  let finishSave; saved.saveResponse(() => new Promise(resolve => { finishSave = resolve; }));
  saved.ui.printTitle.value = "提交名称"; saved.context.markDraftDirty();
  const saving = saved.context.saveDraft(); await tick(); saved.ui.printTitle.value = "后续新名称"; saved.context.markDraftDirty();
  finishSave({ ok: true, json: async () => ({ draft: { id: "saved", title: "提交名称", ids: [...saved.state.basket] } }) }); await saving;
  assert(saved.state.draftDirty, "Late save completion cannot clear a post-submit title change");

  const active = harness(); await active.context.openPrint(); active.ui.printTitle.value = "仍在编辑"; active.context.markDraftDirty();
  await active.click(active.link("/library", { getAttribute: () => "page" }));
  assert.equal(active.confirms.length, 0); assert.equal(active.navigations.length, 0, "Clicking selected navigation is a no-op, preserving filters and draft");
  const count = active.confirms.length;
  for (const changed of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { defaultPrevented: true }]) {
    active.context.handleLibraryNavigation(Object.assign(active.event(active.link()), changed));
  }
  active.context.handleLibraryNavigation(active.event(active.link("/", { target: "_blank" })));
  active.context.handleLibraryNavigation(active.event(active.link("/", { hasAttribute: () => true })));
  active.context.handleLibraryNavigation(active.event(active.link("/library?document=paper#question")));
  assert.equal(active.confirms.length, count, "New tabs, downloads, handled clicks and same-page anchors retain normal behavior");

  const failedNavigation = harness(); await failedNavigation.context.openPrint(); failedNavigation.ui.printTitle.value = "未保存"; failedNavigation.context.markDraftDirty();
  failedNavigation.failAssign(true);
  await assert.rejects(failedNavigation.context.leaveLibraryFor("http://127.0.0.1:8768/"), /navigation blocked/);
  assert(failedNavigation.unload().prevented, "An unsuccessful navigation must not leave a future unload bypass");

  // The real saveBasket path must reconcile a new persisted basket without
  // turning mere basket persistence into a phantom unsaved paper.
  assert.match(source, /if \(state\.draft \|\| state\.draftDirty\) markDraftDirty\(\);\s*else if \(state\.draftBaseline !== null\) state\.draftBaseline = draftSignature\(\);/);
  assert.match(source, /applyPrintOptions\(current\.print_options\);\s*state\.draftBaseline = draftSignature\(\);/);
  assert.match(source, /window\.addEventListener\("beforeunload", protectLibraryBeforeUnload\);\s*document\.addEventListener\("click", handleLibraryNavigation\);/);

  const app = fs.readFileSync(path.join(__dirname, "app.js"), "utf8"), listeners = [];
  // The app uses CRLF on Windows; locate the actual navigation listener via
  // its unique anchor expression without depending on line-ending style.
  const match = app.match(/  document\.addEventListener\("click", \(event\) => \{\s*const link = event\.target\.closest\?\.\("a\[href\]"\);[\s\S]*?\n  \}\);/);
  assert(match, "Exercise the actual review-page navigation listener");
  let appLeaves = 0;
  const appContext = { URL, document: { addEventListener: (name, listener) => listeners.push(listener) }, editGuard: { hasPendingWork: () => true },
    window: { location: { href: "http://127.0.0.1:8768/?paper=kept", pathname: "/" } }, leaveFor: () => { appLeaves++; } };
  vm.createContext(appContext); vm.runInContext(match[0], appContext);
  const same = active.event(active.link("/", { getAttribute: () => "page" })); listeners[0](same);
  assert(same.prevented); assert.equal(appLeaves, 0, "Selected intake link cannot reload or discard current question edits");
  const library = active.event(active.link("/library")); listeners[0](library); assert(library.prevented); assert.equal(appLeaves, 1, "Real review-to-library navigation still uses the edit guard");
  console.log("Library navigation: truthful draft changes, safe in-app leave, one-use approval and real unsaved edits protected: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
