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
const target = (kind = "canvas") => ({ tagName: kind === "input" ? "INPUT" : "DIV",
  closest(selector) {
    if (kind === "canvas" && selector === "#pageStage") return this;
    if (kind === "input" && selector.includes("input")) return this;
    if (kind === "button" && selector.includes("button")) return this;
    if (kind === "contenteditable" && selector.includes("contenteditable")) return this;
    return null;
  }
});
function press(key, extra = {}, kind = "canvas") {
  const event = { key, target: target(kind), preventDefault() { this.defaultPrevented = true; }, ...extra };
  listeners.get("keydown")(event);
  return event;
}
const clone = (value) => JSON.parse(JSON.stringify(value));

press("Enter"); press("Enter", { ctrlKey: true });
assert.deepEqual(clone(calls.splice(0)), [["save", { next: true }], ["save", { complete: true }]]);
for (const extra of [{ repeat: true }, { isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }]) {
  press("Enter", extra); press("Enter", { ctrlKey: true, ...extra });
}
for (const kind of ["input", "contenteditable", "button"]) {
  for (const [key, extra] of [["Enter", {}], ["Enter", { ctrlKey: true }], ["z", { ctrlKey: true }], ["PageDown", {}], ["Delete", {}]]) press(key, extra, kind);
}
assert.deepEqual(calls, [], "Typing, composition, native controls, held Enter and already handled events never save, delete or navigate");
for (const field of ["saving", "closing"]) {
  context.dialog[field] = true; press("Enter"); press("PageDown"); press("z", { ctrlKey: true }); context.dialog[field] = false;
}
context.otherModal = true; press("Enter"); press("z", { ctrlKey: true }); context.otherModal = false;
context.menuOpen = true; press("Enter"); press("Delete"); context.menuOpen = false;
modal.open = false; press("Enter"); modal.open = true;
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
  press("Enter"); assert.equal(calls.length, 0, `${mode} does not accidentally save the next new question`);
  press("Enter", { ctrlKey: true }); assert.deepEqual(clone(calls.splice(0)), [["save", {}]]);
}
context.dialog.mode = "view";
press("Enter", { ctrlKey: true }); press("Delete"); press("z", { ctrlKey: true });
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

console.log("Crop shortcuts: scoped save/next, composition/native controls, modal/save locks, sparse page navigation, undo/redo, pan/zoom and ownership bubbling: OK");
