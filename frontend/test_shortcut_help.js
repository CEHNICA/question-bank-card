"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const help = require("./shortcut-help.js"), { cropShortcutAction } = require("./app.js");
const read = name => fs.readFileSync(path.join(__dirname, name), "utf8");
const keys = value => [...value.primary, ...value.more].flatMap(item => item.keys);
assert.deepEqual(help.reference("review").primary.map(item => item.keys[0]), ["N", "Enter", "K"]);
assert.deepEqual(help.reference("review", { comparison: true }).primary.flatMap(item => item.keys), ["←", "→", "Enter", "Esc"]);
assert(!keys(help.reference("review", { comparison: true })).some(key => ["Q", "O", "1", "Delete"].includes(key)), "A modal only describes keys usable inside that modal");
assert(!keys(help.reference("review")).includes("4")); assert(keys(help.reference("review", { aiFilter: true })).includes("4"));
assert(keys(help.reference("crop")).includes("S") && keys(help.reference("crop")).includes("Ctrl+S"));
assert(!keys(help.reference("crop", { mode: "figures" })).includes("Ctrl+Z"), "Figure editing must not advertise range-only undo");
assert(keys(help.reference("crop", { mode: "regions" })).includes("Ctrl+Z"));
assert(keys(help.reference("crop", { mode: "regions" })).includes("Ctrl+Enter"));
assert(keys(help.reference("crop", { mode: "view" })).includes("Ctrl+Enter"));
assert(keys(help.reference("crop", { mode: "view" })).includes("Shift+Enter"));
assert(!keys(help.reference("crop", { mode: "read", practiceRead: true })).includes("Ctrl+Enter"));
assert(keys(help.reference("answers")).includes("Ctrl+S")); assert(!keys(help.reference("answers")).includes("Ctrl+Enter"));
assert.deepEqual(keys(help.reference("library", { fullScreen: true })), ["←", "→", "Esc", "Ctrl+滚轮"]);
assert(keys(help.reference("library")).includes("/"));
assert(!keys(help.reference("library")).some(key => ["N", "J", "K", "E", "0", "W", "−", "+"].includes(key)));
assert.equal(help.reference("print").primary[0].keys[0], "Esc");

class Element {
  constructor(doc, tag) { this.ownerDocument = doc; this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.events = {}; this.textContent = ""; this.open = false; this.closed = 0; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  showModal() { this.open = true; }
  close() { this.open = false; this.closed++; }
  querySelector(selector) { return all(this).find(value => selector === "[data-shortcut-help-close]" ? Object.hasOwn(value.dataset || {}, "shortcutHelpClose") : value.className?.split(" ").includes(selector.slice(1))) || null; }
}
function all(value) { return [value, ...(value.children || []).flatMap(all)]; }
const doc = { createElement(tag) { return new Element(this, tag); }, createTextNode(text) { return { textContent: text, children: [] }; }, getElementById(id) { return all(this.body).find(value => value.id === id) || null; } };
doc.body = new Element(doc, "body");
const browser = { document: doc }; const browserContext = { window: browser }; vm.createContext(browserContext); vm.runInContext(read("shortcut-help.js"), browserContext);
const host = doc.createElement("span"); host.setAttribute("aria-hidden", "true"); doc.body.append(host);
const inline = browser.QBShortcutHelp.mountHint(host, "crop");
assert.equal(inline.open, false, "Complete inline guidance starts collapsed"); assert.equal(host.attributes["aria-hidden"], undefined, "Interactive help remains accessible");
assert(all(inline).some(value => value.textContent === "Ctrl+S"));
browser.QBShortcutHelp.mountHint(host, "answers"); assert.equal(host.children.length, 1, "Changing scenes does not append duplicate guidance");
assert(!all(host).some(value => value.textContent === "S"));
assert.equal(browser.QBShortcutHelp.open("library"), true); const modal = doc.getElementById("keysDialog");
assert.equal(modal.open, true); const body = doc.getElementById("shortcutHelpBody"); const select = all(body).find(value => value.tagName === "select");
assert.equal(select.value, "library"); assert.equal(select.children.length, 5);
assert.equal(body.querySelector(".shortcut-details").open, false, "The full modal list also starts collapsed");
select.value = "answers"; select.events.change[0](); assert(all(body).some(value => value.textContent === "Ctrl+S"));
assert(!all(body).some(value => value.textContent === "Ctrl+Enter"));
modal.querySelector("[data-shortcut-help-close]").events.click[0](); assert.equal(modal.open, false);
browser.QBShortcutHelp.open("review"); assert.equal(doc.getElementById("keysDialog"), modal);
assert.equal(modal.querySelector("[data-shortcut-help-close]").events.click.length, 1, "Opening help repeatedly keeps one close binding");

const target = kind => ({ closest: selector => kind && selector.includes(kind) ? {} : null });
const event = (key, extra = {}) => ({ key, target: target(), preventDefault() { this.prevented = true; }, ...extra });
for (const extra of [{ isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }, { ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { target: target("input") }, { target: target('role="textbox"') }, { target: target("contenteditable") }]) assert(help.ordinaryKeyBlocked(event("n", extra)));
assert(!help.ordinaryKeyBlocked(event("O", { shiftKey: true }), { allowShift: true }));
const canvas = { open: true, mode: "new", canvasFocused: true };
assert.equal(cropShortcutAction(event("s"), canvas), "next"); assert.equal(cropShortcutAction(event("s", { ctrlKey: true }), canvas), "complete");
for (const extra of [{ repeat: true }, { isComposing: true }, { keyCode: 229 }, { altKey: true }, { shiftKey: true }]) assert.equal(cropShortcutAction(event("s", extra), canvas), null);
assert.equal(cropShortcutAction(event("s", { ctrlKey: true }), { ...canvas, editing: true }), null);
assert.equal(cropShortcutAction(event("s", { ctrlKey: true }), { ...canvas, canvasFocused: false }), "complete");
assert.match(help.reference("crop").extra, /新增题画框时光标变为十字/);
assert.match(help.reference("crop").extra, /双击已保存题框返回对应审核卡/);

const app = read("app.js"), viewerApprove = app.slice(app.indexOf("  async function viewerApprove()"), app.indexOf("  function viewerKey("));
const viewerKeys = app.slice(app.indexOf("  function viewerKey("), app.indexOf("  function editFromViewer("));
async function approvalChecks() {
  const q1 = { id: 1, approved: true }, q2 = { id: 2, approved: false }, changes = [], steps = [];
  const context = { viewer: { id: 1 }, state: { questions: [q1, q2] }, questionById: id => [q1, q2].find(q => q.id === id),
    isHumanApproved: q => q.approved, figureBlocksApproval: () => false, typeBlocksApproval: () => false, canApprove: () => true,
    viewerList: () => [q1, q2], visible: () => true, viewerStep: value => { steps.push(value); },
    approveQuestion: async (q, value) => { changes.push([q.id, value]); q.approved = value; return true; }, setCurrent() {}, hideLens() {}, renderPaper() {}, renderViewer() {}, toast() {},
    // 1.12.6: U now goes through revokeQuestion, which also withdraws the library
    // copy.  The harness records the same [id, false] shape so the assertion below
    // still describes "U is the explicit revocation key".
    revokeQuestion: async (q) => { changes.push([q.id, false]); q.approved = false; return true; },
    isSettled: q => Boolean(q.publication && q.publication.up_to_date),
    QBUpload: { isEditingTarget: help.isEditingTarget }, isApproved: q => q.approved, $: () => ({ scrollTo() {} }) };
  vm.createContext(context); vm.runInContext(viewerApprove + viewerKeys, context);
  await context.viewerApprove(); assert.deepEqual(steps, [1]); assert.equal(changes.length, 0, "Enter on an approved question advances without any approval mutation");
  context.viewer.id = 2; await context.viewerApprove(); assert.deepEqual(changes, [[2, true]], "An unapproved question still uses the ordinary approval path");
  context.viewer.id = 1; context.viewerKey(event("u")); assert.deepEqual(changes.at(-1), [1, false], "U is the explicit revocation key");
  const before = changes.length, navigation = steps.length;
  for (const extra of [{ repeat: true }, { isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { target: target("input") }, { target: target('role="textbox"') }, { target: target("button") }]) context.viewerKey(event("Enter", extra));
  assert.equal(changes.length, before); assert.equal(steps.length, navigation, "Typing, modifier keys, repeats and native button activation do not invoke viewer approval");
  let mainKey, mainMoves = 0, viewerKeysCalled = 0, modalOpen = false, otherModal = false;
  const marker = app.indexOf('  document.addEventListener("keydown", (event) => {', app.indexOf("  function openShortcutHelp("));
  const mainContext = { ...context, state: { current: 1 }, document: { addEventListener: (_, callback) => mainKey = callback, querySelector: () => otherModal ? {} : null },
    $: id => id === "viewerDialog" ? { open: modalOpen } : { hidden: false }, anyDialogOpen: () => false, moveNextCard: () => mainMoves++, viewerKey: () => viewerKeysCalled++ };
  q1.approved = true;
  vm.runInNewContext(app.slice(marker, app.indexOf("  // ---------------------------------------------------------------- 原卷截图", marker)), mainContext);
  mainKey(event("Enter")); assert.equal(mainMoves, 1); assert.equal(changes.length, before, "The main list also advances an approved question without revoking it");
  for (const extra of [{ repeat: true }, { shiftKey: true }, { ctrlKey: true }, { isComposing: true }, { target: target("input") }, { target: target("button") }]) mainKey(event("Enter", extra));
  assert.equal(mainMoves, 1);
  modalOpen = true; otherModal = true; mainKey(event("Enter")); assert.equal(viewerKeysCalled, 0, "Help over a comparison modal cannot approve the covered question");
  otherModal = false; mainKey(event("Enter")); assert.equal(viewerKeysCalled, 1);
  let editKey, submits = 0; const editorMarker = app.indexOf('    editor.addEventListener("keydown", (event) => {');
  vm.runInNewContext(app.slice(editorMarker, app.indexOf("    return editor", editorMarker)).split('\n    });')[0] + '\n    });', { editor: { addEventListener: (_, callback) => editKey = callback, requestSubmit: () => submits++ } });
  editKey(event("Enter", { ctrlKey: true, target: target("textarea") })); assert.equal(submits, 1);
  for (const extra of [{ repeat: true }, { shiftKey: true }, { altKey: true }, { isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }]) editKey(event("Enter", { ctrlKey: true, ...extra }));
  assert.equal(submits, 1, "Audit edit saving accepts Ctrl+Enter while typing, with no IME or repeated submission");
}

function libraryChecks() {
  const source = read("library.js"), start = source.lastIndexOf('  document.addEventListener("keydown", (event) => {');
  const code = source.slice(start, source.indexOf("  if (state.document)", start));
  let handler, searches = 0, helps = 0, dialog = false; const search = { disabled: false, focus() { searches++; } };
  const context = { document: { activeElement: target(), addEventListener(type, callback) { handler = callback; }, querySelector() { return dialog ? {} : null; } },
    ui: { sheet: { hidden: true }, search }, window: { QBShortcutHelp: { open: () => helps++ } }, handlePrintEscape() {} };
  vm.runInNewContext(code, context); handler(event("/")); assert.equal(searches, 1); handler(event("?", { shiftKey: true })); assert.equal(helps, 1);
  for (const extra of [{ defaultPrevented: true }, { isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { repeat: true }, { target: target("input") }, { target: target("contenteditable") }, { target: target('role="textbox"') }]) handler(event("/", extra));
  dialog = true; handler(event("/")); assert.equal(searches, 1, "Search cannot steal input, IME, modified browser shortcuts or a modal's focus");
  const editor = read("library-answer-editor.js"), marker = editor.indexOf('      dialog.addEventListener("keydown", event => {'); let saveHandler, saves = 0;
  vm.runInNewContext(editor.slice(marker, editor.indexOf('      cropSurface.addEventListener("click"', marker)), { dialog: { addEventListener: (_, callback) => saveHandler = callback }, saveCurrent: () => saves++ });
  saveHandler(event("s", { ctrlKey: true, target: target("textarea") })); assert.equal(saves, 1, "Answer saving remains available while typing");
  for (const extra of [{ isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }, { altKey: true }, { shiftKey: true }, { repeat: true }]) saveHandler(event("s", { ctrlKey: true, ...extra }));
  assert.equal(saves, 1, "Answer-save repeats, IME and other combinations cannot submit again");
  const viewer = read("library-question-viewer.js"), keyStart = viewer.indexOf('      dialog.addEventListener("keydown", event => {'); let navHandler; const moves = [];
  vm.runInNewContext(viewer.slice(keyStart, viewer.indexOf('      viewport.addEventListener("wheel"', keyStart)), { dialog: { open: true, addEventListener: (_, callback) => navHandler = callback }, navigation: {}, navigate: step => moves.push(step) });
  navHandler(event("ArrowRight")); navHandler(event("ArrowLeft")); assert.deepEqual(moves, [1, -1]);
  for (const extra of [{ repeat: true }, { defaultPrevented: true }, { isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { target: target("input") }, { target: target("contenteditable") }]) navHandler(event("ArrowRight", extra));
  assert.deepEqual(moves, [1, -1]);
}
approvalChecks().then(() => { libraryChecks(); console.log("Scene shortcuts: current compact help, mode-specific complete reference, safe Enter/U and input/IME/modifier/repeat guards: OK"); }).catch(error => { console.error(error); process.exitCode = 1; });
