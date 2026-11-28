"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const start = source.indexOf("  function clearLayoutCenterMargins(");
const end = source.indexOf("  function pointFrom(", start);
assert.ok(start >= 0 && end > start, "centering helpers stay together");
const zoomStart = source.indexOf("  function requestPageZoom(");
const zoomEnd = source.indexOf("  function zoomPageBy(", zoomStart);
assert.ok(zoomStart >= 0 && zoomEnd > zoomStart);

function box(id, piece, width, height) {
  return {
    dataset: { layoutSourceQuestionId: String(id), layoutCoveragePiece: String(piece) },
    getBoundingClientRect: () => ({ left: 120, top: 80, width, height })
  };
}

function harness() {
  const scrolls = [];
  const stage = {
    clientWidth: 800,
    clientHeight: 600,
    scrollLeft: 0,
    scrollTop: 0,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    scrollTo(pos) { scrolls.push(pos); this.scrollLeft = pos.left; this.scrollTop = pos.top; },
    querySelector: () => surface
  };
  const surface = {
    isConnected: true,
    style: { margin: "240px 360px" },
    boxes: [],
    querySelectorAll: () => surface.boxes
  };
  const pageDialog = { open: true };
  const dialog = {
    session: 3, drag: null, zoomFrame: 0, zoomMode: "width", layoutWorkspace: true,
    layoutFocus: { questionId: 7, index: 0 }, layoutCenterMargins: true
  };
  const frames = [];
  const context = {
    dialog, stage, surface, scrolls, frames,
    requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
    cancelAnimationFrame() {},
    applyPageZoom() { context.zooms = (context.zooms || 0) + 1; },
    $(id) { return id === "pageDialog" ? pageDialog : id === "pageStage" ? stage : null; }
  };
  vm.runInNewContext(`${source.slice(start, end)}\n${source.slice(zoomStart, zoomEnd)}`, context);
  return context;
}

const missing = harness();
missing.surface.boxes = [box(7, 1, 40, 20)];
missing.centerLayoutFocus(missing.surface);
assert.equal(missing.dialog.layoutCenterMargins, false, "a piece on another index must not keep the half-screen margin");
assert.equal(missing.surface.style.margin, "", "the page returns to the stage instead of sitting in the lower right");
assert.equal(missing.dialog.layoutFocus, null);

const ready = harness();
ready.surface.boxes = [box(7, 0, 80, 40)];
ready.centerLayoutFocus(ready.surface);
assert.equal(ready.dialog.layoutCenterMargins, true);
assert.match(ready.surface.style.margin, /px/, "a real box on this page can still be centered");
assert.equal(ready.frames.length, 1);
ready.frames[0]();
assert.equal(ready.scrolls.length, 1);
assert.ok(ready.scrolls[0].left >= 0 && ready.scrolls[0].top >= 0);

const fitted = harness();
fitted.surface.boxes = [box(7, 0, 80, 40)];
assert.equal(fitted.requestPageZoom("fit"), true);
assert.equal(fitted.frames.length, 1);
fitted.frames[0]();
assert.equal(fitted.frames.length, 2);
fitted.frames[1]();
assert.equal(fitted.dialog.zoomMode, "fit");
assert.equal(fitted.dialog.layoutCenterMargins, false, "适页 clears the centering margin");
assert.equal(fitted.surface.style.margin, "");
assert.equal(fitted.scrolls.at(-1).top, 0);
assert.equal(fitted.scrolls.at(-1).left, 0);
assert.equal(fitted.dialog.layoutFocus.questionId, 7, "an explicit zoom does not also jump to a question");

const closed = harness();
closed.$("pageDialog").open = false;
closed.centerLayoutFocus(closed.surface);
assert.equal(closed.surface.style.margin, "240px 360px", "a hidden dialog must not measure a zero-size stage into the margin");

console.log("page mode zoom: missing piece, real box, fit reset, closed dialog: OK");
