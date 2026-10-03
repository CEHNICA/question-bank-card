"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("./library.js"), "utf8");
const start = source.indexOf("  function closePrintTools("), end = source.indexOf("  function printTools(", start);
assert.ok(start >= 0 && end > start);
const listeners = new Map(), menus = [], focused = [];
const context = {
  ui: { sheet: { hidden: false }, paper: { querySelectorAll: () => menus.filter(menu => menu.open) } },
  printState: { activeToolId: null, optionOverrides: { a: "vertical" }, answerSpaceOverrides: { a: "small" }, questionBreaks: ["a"], token: 8 },
  document: { addEventListener: (name, callback) => listeners.set(name, callback), querySelector: () => context.otherDialog ? {} : null },
  closePrint: () => { context.closed = (context.closed || 0) + 1; }
};
vm.runInNewContext(source.slice(start, end), context);
assert.match(source.slice(source.lastIndexOf('  document.addEventListener("keydown"')), /handlePrintEscape\(event\);/, "The existing preview keyboard handler uses the layered menu dismissal");
const menu = (id) => {
  const control = { id: `${id}-control` }, summary = { id: `${id}-summary`, focus: options => focused.push({ id, options }) };
  const result = { id, open: true, contains: target => target === result || target === control || target === summary,
    querySelector: selector => selector === "summary" ? summary : null, control, summary };
  menus.push(result); context.printState.activeToolId = id;
  return result;
};
const event = (extra = {}) => ({ key: "Escape", button: 0, target: {},
  preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra });
const before = JSON.stringify({ overrides: context.printState.optionOverrides, space: context.printState.answerSpaceOverrides,
  breaks: context.printState.questionBreaks, token: context.printState.token });

const first = menu("a");
for (const target of [first, first.control, first.summary]) listeners.get("pointerdown")(event({ target }));
assert.equal(first.open, true, "Summary/select/action clicks inside the current menu remain usable");
listeners.get("pointerdown")(event({ button: 2 }));
assert.equal(first.open, true, "A context click does not steal the active menu");
const blankClick = event(); listeners.get("pointerdown")(blankClick);
assert.equal(first.open, false); assert.equal(context.printState.activeToolId, null);
assert.equal(blankClick.defaultPrevented, undefined); assert.equal(blankClick.stopped, undefined);
assert.equal(focused.length, 0, "Clicking a title input, blank paper or export action keeps that click and its focus behavior");
assert.equal(context.closed, undefined, "An outside click dismisses the menu without leaving the preview");

// Two disclosures can briefly coexist before their native toggle events.
const second = menu("b"), third = menu("c");
listeners.get("pointerdown")(event({ button: undefined }));
assert.equal(second.open || third.open, false, "Touch/pen outside gestures close all currently open disclosures");
assert.equal(context.printState.activeToolId, null, "The state clears synchronously so layout redraw cannot reopen a dismissed menu");
const fourth = menu("d"); context.ui.sheet.hidden = true;
listeners.get("pointerdown")(event()); assert.equal(fourth.open, true);
context.ui.sheet.hidden = false;

const escape = event(); context.handlePrintEscape(escape);
assert.equal(fourth.open, false); assert.equal(context.closed, undefined);
assert.equal(escape.defaultPrevented, true); assert.equal(escape.stopped, true, "First Esc cannot also trigger the dialog's native dismissal");
assert.equal(focused.at(-1).id, "d"); assert.equal(focused.at(-1).options.preventScroll, true);
const secondEscape = event(); context.handlePrintEscape(secondEscape);
assert.equal(context.closed, 1, "The following Esc returns through the existing guarded closePrint flow");
assert.equal(secondEscape.defaultPrevented, true);

const protectedMenu = menu("e");
for (const extra of [{ isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }, { key: "Enter" }]) context.handlePrintEscape(event(extra));
context.otherDialog = true; context.handlePrintEscape(event()); context.otherDialog = false;
context.ui.sheet.hidden = true; context.handlePrintEscape(event()); context.ui.sheet.hidden = false;
assert.equal(protectedMenu.open, true); assert.equal(context.closed, 1, "An editor/confirmation dialog, composition or handled event does not close the underlying menu or preview");
assert.equal(JSON.stringify({ overrides: context.printState.optionOverrides, space: context.printState.answerSpaceOverrides,
  breaks: context.printState.questionBreaks, token: context.printState.token }), before,
"Dismissal never changes order, page breaks, single-question options, writing space or layout tokens");

console.log("Print adjustment menus: blank/touch dismissal, inner controls, synchronous redraw state, first/second Esc, focus, dialog/IME guards and unchanged paper settings: OK");
