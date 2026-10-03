"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const start = source.indexOf('  $("pageDialog").addEventListener("keydown", (event) => {');
const end = source.indexOf('  $("manualProcessing").addEventListener', start);
assert.ok(start > 0 && end > start);
const listeners = new Map(), calls = [];
const stage = { classList: { add: (value) => calls.push(["class", value]) } };
const modal = { open: true, addEventListener: (name, listener) => listeners.set(name, listener) };
const context = {
  $: (id) => id === "pageDialog" ? modal : stage,
  QBManualCrop: App, QBUpload: App,
  dialog: { mode: "new", selected: 0, saving: false, closing: false, page: 2 },
  state: { paper: { pages: [{ page_idx: 0 }, { page_idx: 2 }, { page_idx: 4 }] } },
  document: { querySelector: () => context.otherModal ? {} : null },
  menuIsOpen: () => Boolean(context.menuOpen),
  savePageCrop: (options = {}) => { calls.push(["save", options]); return Promise.resolve(); },
  restoreDialogBoxes: (redo) => calls.push(["history", redo]),
  dialogPageIndex: () => context.state.paper.pages.findIndex((page) => page.page_idx === context.dialog.page),
  goToDialogPage: (page) => { context.dialog.page = page; calls.push(["page", page]); },
  cancelFigureSketch: () => { context.dialog.sketch = null; calls.push(["cancel-sketch"]); },
  zoomPageBy: (factor) => calls.push(["zoom", factor]),
  requestPageZoom: (mode) => calls.push(["zoom-mode", mode]),
  removeBox: (index) => calls.push(["remove", index])
};
vm.runInNewContext(source.slice(start, end), context);
const target = (kind = "canvas") => ({ tagName: ["input", "textarea", "select", "button", "summary", "a"].includes(kind) ? kind.toUpperCase() : "DIV",
  closest(selector) {
    if (kind === "canvas" && selector === "#pageStage") return this;
    if (["input", "textarea", "select", "button", "summary", "a"].includes(kind) && selector.split(/[, ]+/).includes(kind)) return this;
    if (kind === "contenteditable" && selector.includes("contenteditable")) return this;
    return null;
  }
});
function press(key, extra = {}, kind = "canvas") {
  const event = { key, target: target(kind), preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra };
  listeners.get("keydown")(event);
  return event;
}
const clone = (value) => JSON.parse(JSON.stringify(value));

press("Enter"); press("Enter", { ctrlKey: true });
assert.deepEqual(clone(calls.splice(0)), [["save", { next: true }], ["save", { complete: true }]]);
assert.equal(press("s").stopped, true);
press("S"); press("s", { ctrlKey: true }); press("s", { metaKey: true });
assert.deepEqual(clone(calls.splice(0)), [["save", { next: true }], ["save", { next: true }], ["save", { complete: true }], ["save", { complete: true }]], "Left-hand S/Ctrl+S are primary while Caps Lock and the existing Enter actions remain compatible");
for (const extra of [{ repeat: true }, { isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }]) {
  press("Enter", extra); press("Enter", { ctrlKey: true, ...extra });
  press("s", extra); press("s", { ctrlKey: true, ...extra });
}
for (const kind of ["input", "textarea", "select", "contenteditable", "button", "summary", "a"]) {
  for (const [key, extra] of [["Enter", {}], ["Enter", { ctrlKey: true }], ["s", {}], ["s", { ctrlKey: true }], ["z", { ctrlKey: true }], ["PageDown", {}], ["Delete", {}]]) press(key, extra, kind);
}
assert.deepEqual(calls, [], "Typing, composition, native controls, held save keys and already handled events never save, delete or navigate");
assert.equal(press("s", { ctrlKey: true }, "input").defaultPrevented, true, "Blocked Ctrl+S never opens the browser Save Page prompt instead of saving a question");
assert.equal(press("s", { ctrlKey: true, isComposing: true }, "input").defaultPrevented, undefined, "IME events remain untouched");
press("s", {}, "outside"); press("s", { ctrlKey: true }, "outside");
for (const extra of [{ shiftKey: true }, { altKey: true }, { ctrlKey: true, shiftKey: true }, { ctrlKey: true, altKey: true }]) press("s", extra);
assert.equal(calls.length, 0, "S requires canvas focus; Shift/Alt combinations cannot complete a paper accidentally");
for (const field of ["saving", "closing"]) {
  context.dialog[field] = true; press("Enter"); press("s"); press("s", { ctrlKey: true }); press("PageDown"); press("z", { ctrlKey: true }); context.dialog[field] = false;
}
context.otherModal = true; press("Enter"); press("s"); assert.equal(press("s", { ctrlKey: true }).defaultPrevented, undefined); press("z", { ctrlKey: true }); context.otherModal = false;
context.menuOpen = true; press("Enter"); press("s"); press("s", { ctrlKey: true }); press("Delete"); context.menuOpen = false;
modal.open = false; press("Enter"); press("s"); press("s", { ctrlKey: true }); modal.open = true;
assert.deepEqual(calls, [], "Another dialog, ownership menu, closing or saving locks out crop shortcuts");

press("z", { ctrlKey: true }); press("Z", { ctrlKey: true, shiftKey: true });
press("PageDown"); press("PageDown"); press("PageUp");
assert.deepEqual(calls.splice(0), [["history", false], ["history", true], ["page", 4], ["page", 2]], "Undo/redo and sparse page navigation have separate actions and stop at page bounds");
context.dialog.sketch = {};
press("Delete"); assert.equal(calls.length, 0, "Delete cannot erase a saved range while an unfinished range is being drawn");
press(" "); press("Delete"); press("+"); press("-"); press("0"); press("w");
assert.deepEqual(calls.splice(0), [["cancel-sketch"], ["class", "pan-ready"], ["remove", 0], ["zoom", 1.25], ["zoom", 0.8], ["zoom-mode", "fit"], ["zoom-mode", "width"]]);
for (const mode of ["regions", "figures", "read"]) {
  context.dialog.mode = mode;
  press("Enter"); press("s"); press("s", { ctrlKey: true }); assert.equal(calls.length, 0, `${mode} does not accidentally save the next new question or consume S for figure ownership`);
  press("Enter", { ctrlKey: true }); assert.deepEqual(clone(calls.splice(0)), [["save", {}]]);
}
context.dialog.mode = "view";
press("Enter", { ctrlKey: true }); press("s"); press("s", { ctrlKey: true }); press("Delete"); press("z", { ctrlKey: true });
assert.equal(calls.length, 0, "A read-only original cannot submit crop edits");

const slotStart = source.indexOf("  const SLOT_KEYS =");
const slotEnd = source.indexOf('  $("figureSlotMenu").addEventListener("toggle"', slotStart);
const slotListeners = new Map(), slots = [];
const slotContext = {
  $: () => ({ addEventListener: (name, listener) => slotListeners.set(name, listener) }),
  dialog: { saving: false }, chooseFigureSlot: (slot) => slots.push(slot), closeFigureSlotMenu: () => slots.push("close")
};
vm.runInNewContext(source.slice(slotStart, slotEnd), slotContext);
for (const key of ["a", "b", "s", "Escape"]) {
  const event = { key, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
  slotListeners.get("keydown")(event);
  assert.equal(event.prevented, true); assert.equal(event.stopped, true, "Slot shortcuts cannot bubble into review or closing actions");
}
assert.deepEqual(slots, ["A", "B", "stem", "close"]);
slotListeners.get("keydown")({ key: "a", isComposing: true });
slotContext.dialog.saving = true; slotListeners.get("keydown")({ key: "a" });
assert.equal(slots.length, 4, "Composition and saving cannot reassign a figure");

const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
assert.match(html, /id="pageDialogSaveNext"[^>]*aria-keyshortcuts="S Enter"[^>]*>保存下一题<span class="kbd-hint">S<\/span>/);
assert.match(html, /id="pageDialogComplete"[^>]*aria-keyshortcuts="Control\+S Control\+Enter"[^>]*>完成切题<span class="kbd-hint">Ctrl\+S<\/span>/);

console.log("Crop shortcuts: left-hand S/Ctrl+S, Enter compatibility, browser-save suppression, scoped input/IME/modal/save locks, page navigation, undo/redo and S figure ownership: OK");
