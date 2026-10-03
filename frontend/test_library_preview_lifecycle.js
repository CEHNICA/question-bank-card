"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
class Element {
  constructor(tag, cls = "", text = "") { this.tagName = tag; this.className = cls; this.textContent = text; this.children = []; this.dataset = {}; this.style = { setProperty() {} }; this.hidden = false; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  querySelectorAll() { return []; }
}
function setup() {
  const controls = new Map(), pending = [];
  const get = id => { if (!controls.has(id)) controls.set(id, new Element("div")); return controls.get(id); };
  const node = (...args) => new Element(...args), ui = { paper: node("article"), sheet: { open: true, hidden: false, close() { this.open = false; } },
    printTitle: { value: "Test" }, printOrigin: { checked: false }, printAnswers: { checked: false }, search: { focus() {} } };
  const printState = { token: 0, layoutToken: 0, layoutPending: false, layoutError: "", missing: [], items: [{ id: "new", question_type: "single_choice" }], exporting: false };
  let assets = () => Promise.resolve();
  const context = { ui, printState, state: { draftSaving: false }, $: get, node, Promise, Set,
    currentPrintOptions: () => ({ font_size: 12, answer_space: "none", document: "questions", student_info: false }),
    renderPrintMissing() {}, syncPrintAnswers() {}, syncExportButtons() {}, preparePrintLayout() {}, toast() {},
    waitForPrintAssets: () => assets(), document: { body: { classList: { remove() {} } } },
    window: { ExamLayout: { paginate: async flow => { const value = deferred(); pending.push({ flow, ...value }); return value.promise; }, scale() {} } } };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("  function renderPrint("), source.indexOf("  function syncIndividualLayout(")), context);
  vm.runInContext(source.slice(source.indexOf("  async function refreshPrintPages("), source.indexOf("  // Width fitting happens")), context);
  vm.runInContext(source.slice(source.indexOf("  function closePrint("), source.indexOf("  let libraryNavigationPending")), context);
  return { context, ui, printState, get, node, pending, assets: fn => { assets = fn; },
    complete: (index, label) => pending[index].resolve({ page_count: 1, pages: [node("article", "exam-page", label)], warnings: [] }) };
}
(async () => {
  const emptied = setup(), oldFlow = emptied.node("div", "print-flow", "old questions");
  const old = emptied.context.refreshPrintPages(oldFlow); await flush();
  assert.equal(emptied.pending.length, 1); assert.equal(emptied.printState.layoutPending, true);
  emptied.context.renderPrint([]); const emptyChildren = [...emptied.ui.paper.children];
  emptied.complete(0, "late old page"); await old;
  assert.deepEqual(emptied.ui.paper.children, emptyChildren, "Late pagination cannot resurrect questions after an empty basket render");
  assert.equal(emptied.printState.layoutPending, false); assert.equal(emptied.get("printPageStatus").textContent, "");
  assert.equal(emptied.ui.paper.dataset.pageCount, undefined);

  const closed = setup(); const closedWork = closed.context.refreshPrintPages(closed.node("div")); await flush();
  const beforeClose = [...closed.ui.paper.children]; closed.context.closePrint(); closed.complete(0, "closed page"); await closedWork;
  assert.equal(closed.ui.sheet.open, false); assert.deepEqual(closed.ui.paper.children, beforeClose);
  assert.equal(closed.printState.layoutPending, false);

  const reopened = setup(); const previous = reopened.context.refreshPrintPages(reopened.node("div", "", "previous")); await flush();
  reopened.context.closePrint(); reopened.ui.sheet.open = true; reopened.ui.sheet.hidden = false;
  const placeholder = reopened.node("p", "", "loading new paper"); reopened.ui.paper.replaceChildren(placeholder);
  const latest = reopened.context.refreshPrintPages(reopened.node("div", "", "latest")); await flush();
  reopened.complete(0, "old response"); await previous;
  assert.deepEqual(reopened.ui.paper.children, [placeholder], "Old pages cannot replace a reopened paper's loading placeholder");
  assert.equal(reopened.printState.layoutPending, true, "Old finally must not clear the new paginator's busy state");
  reopened.complete(1, "new response"); await latest;
  assert.equal(reopened.ui.paper.children[0].textContent, "new response"); assert.equal(reopened.printState.layoutPending, false);

  const fonts = setup(), fontWait = deferred(); fonts.assets(() => fontWait.promise);
  const waiting = fonts.context.refreshPrintPages(fonts.node("div")); await flush(); fonts.context.renderPrint([]);
  fontWait.resolve(); await waiting;
  assert.equal(fonts.pending.length, 0, "An invalidated font/image wait never begins stale pagination");

  const hidden = setup(); const hideWork = hidden.context.refreshPrintPages(hidden.node("div")); await flush();
  hidden.ui.sheet.open = false; hidden.complete(0, "hidden late response"); await hideWork;
  assert.equal(hidden.ui.paper.children.length, 0, "Native dialog closure also prevents a late page replacement");
  console.log("Print preview lifecycle: late pages/fonts cannot revive emptied, closed or reopened paper: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
