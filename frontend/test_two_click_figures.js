"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const functions = source.slice(source.indexOf("  function pointFrom(event, surface)"),
  source.indexOf("  function readingOrder(boxes)", source.indexOf("  function pointFrom(event, surface)")));
const surfaceEvents = source.slice(source.indexOf("    // While a new figure is being outlined"),
  source.indexOf("    stage.append(surface);", source.indexOf("    // While a new figure is being outlined")));
const pageCancel = source.slice(source.indexOf('  $("pageDialog").addEventListener("cancel"'),
  source.indexOf("  function openPageDialog("));
const save = source.slice(source.indexOf("  async function savePageCrop("),
  source.indexOf('  $("pageDialogSave").addEventListener("click"'));
const detached = (value) => JSON.parse(JSON.stringify(value));

class Node {
  constructor(classes = "") {
    this.children = []; this.isConnected = true; this.events = new Map(); this.attrs = {};
    const values = new Set(classes.split(/\s+/).filter(Boolean));
    this.classList = { add: (...items) => items.forEach((item) => values.add(item)),
      remove: (...items) => items.forEach((item) => values.delete(item)), contains: (item) => values.has(item) };
  }
  append(node) { this.children.push(node); node.parent = this; }
  remove() { this.isConnected = false; if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this); }
  focus() {}
  setAttribute(key, value) { this.attrs[key] = value; }
  getBoundingClientRect() { return this.rect || { left: 100, top: 50, width: 500, height: 1000 }; }
  querySelector() { return this.frame; }
  addEventListener(name, handler, options) {
    if (!this.events.has(name)) this.events.set(name, []);
    this.events.get(name).push({ handler, capture: Boolean(options?.capture) });
  }
  emit(name, event) {
    for (const capture of [true, false]) for (const item of this.events.get(name) || []) {
      if (item.capture !== capture || event.stopped) continue;
      item.handler(event);
    }
  }
}

function harness(mode = "figures") {
  const listeners = new Map(), statuses = [], assignments = [], history = [];
  const surface = new Node(), image = new Node(); surface.append(image);
  const nodes = { pageDialog: new Node(), pageStage: new Node() }; nodes.pageDialog.open = true;
  const context = {
    surface, image,
    dialog: { mode, tool: "draw", saving: false, imageReady: true, boxes: [], page: 2,
      session: 7, selected: null, spacePan: false, drag: null, sketch: null, paperId: "paper" },
    state: { paperId: "paper", paper: {} },
    window: {
      addEventListener(name, handler) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(handler); },
      removeEventListener(name, handler) { listeners.get(name)?.delete(handler); }
    },
    $: (id) => nodes[id] || (nodes[id] = new Node()),
    el: (_tag, classes) => new Node(classes),
    placeBox: (node, bbox) => { node.bbox = [...bbox]; },
    showCropResult: (message, error = false) => statuses.push({ message, error }),
    copyDialogBoxes: () => context.dialog.boxes.map((box) => ({ ...box, bbox: [...box.bbox] })),
    rememberDialogBoxes: (before) => history.push(detached(before)),
    renderStage() { context.renders = (context.renders || 0) + 1; context.cancelFigureSketch(); },
    renderPageTabs() {}, menuIsOpen: () => Boolean(context.dialog.pendingFigure),
    closeFigureSlotMenu() { context.dialog.pendingFigure = null; context.dialog.slotAnchor = null; }, toast() {},
    openFigureSlotMenu(preview, target) {
      context.dialog.pendingFigure = target; context.dialog.slotAnchor = preview;
      assignments.push({ preview, target: detached(target) });
    },
    requestPageDialogClose: () => { context.closeRequests = (context.closeRequests || 0) + 1; }
  };
  vm.runInNewContext(functions + surfaceEvents + pageCancel + save, context);
  const event = (x, y, extra = {}) => ({ clientX: x, clientY: y, button: 0, pointerId: 1,
    target: surface, currentTarget: surface, preventDefault() { this.prevented = true; },
    stopPropagation() { this.stopped = true; }, stopImmediatePropagation() { this.stopped = true; }, ...extra });
  const emitWindow = (name, item) => [...listeners.get(name) || []].forEach((handler) => handler(item));
  return { context, surface, image, nodes, listeners, statuses, assignments, history, event, emitWindow };
}

(async () => {
  const basic = harness();
  basic.surface.emit("pointerdown", basic.event(150, 150));
  assert.ok(basic.context.dialog.sketch, "First click starts a separate preview");
  assert.equal(basic.surface.children.length, 2, "The first click immediately shows an outline");
  assert.deepEqual(detached(basic.context.dialog.boxes), [], "An unfinished rectangle is not a saved figure");
  assert.equal(basic.context.dialog.drag, null, "The mouse does not need to stay pressed");
  basic.emitWindow("pointerup", basic.event(150, 150));
  assert.ok(basic.context.dialog.sketch, "Releasing the first click does not finish it");
  basic.emitWindow("pointermove", basic.event(300, 400, { buttons: 0 }));
  assert.deepEqual(basic.surface.children[1].bbox, [100, 100, 400, 350], "Released mouse motion previews normalized coordinates");
  basic.surface.emit("pointerdown", basic.event(300, 400));
  assert.equal(basic.context.dialog.sketch, null);
  assert.equal(basic.assignments.length, 1, "Only the second click opens figure ownership selection");
  assert.deepEqual(basic.assignments[0].target, { kind: "new", box: { page_idx: 2, bbox: [100, 100, 400, 350] } });
  assert.equal(basic.assignments[0].preview.classList.contains("pending-assignment"), true);
  assert.equal(basic.assignments[0].preview.classList.contains("drawing"), false);
  const finalClick = basic.event(300, 400); basic.surface.emit("click", finalClick);
  assert.equal(finalClick.stopped, true, "The completion click cannot also activate an underlying candidate or label");
  assert.equal([...basic.listeners.values()].every((set) => set.size === 0), true, "Completion removes temporary window listeners");

  const backwards = harness();
  backwards.surface.emit("pointerdown", backwards.event(500, 900));
  backwards.surface.emit("pointerdown", backwards.event(-50, -20));
  assert.deepEqual(backwards.assignments[0].target.box.bbox, [0, 0, 800, 850], "Reverse direction and image edges are clamped");

  const zoomed = harness();
  zoomed.surface.emit("pointerdown", zoomed.event(150, 150));
  zoomed.surface.rect = { left: 20, top: 30, width: 1000, height: 2000 };
  zoomed.surface.emit("pointerdown", zoomed.event(420, 730));
  assert.deepEqual(zoomed.assignments[0].target.box.bbox, [100, 100, 400, 350], "A zoom/pan between clicks uses the current image rectangle");

  const tiny = harness();
  tiny.surface.emit("pointerdown", tiny.event(150, 150));
  tiny.surface.emit("pointerdown", tiny.event(151, 151));
  assert.ok(tiny.context.dialog.sketch); assert.equal(tiny.assignments.length, 0);
  assert.match(tiny.statuses.at(-1).message, /范围太小/);
  tiny.surface.emit("pointerdown", tiny.event(300, 400));
  assert.equal(tiny.assignments.length, 1, "An accidental tiny click can be corrected without restarting");

  const overCandidate = harness();
  overCandidate.surface.emit("pointerdown", overCandidate.event(150, 150));
  const candidate = new Node("candidate");
  const endOnCandidate = overCandidate.event(300, 400, { target: candidate });
  overCandidate.surface.emit("pointerdown", endOnCandidate);
  assert.equal(endOnCandidate.stopped, true);
  assert.deepEqual(overCandidate.assignments[0].target.box.bbox, [100, 100, 400, 350], "The second corner can land on an existing overlay");

  for (const cause of ["blur", "pointercancel", "render", "Escape", "middle-button"]) {
    const cancelled = harness(); cancelled.surface.emit("pointerdown", cancelled.event(150, 150));
    if (cause === "render") cancelled.context.renderStage();
    else if (cause === "Escape") cancelled.nodes.pageDialog.emit("cancel", cancelled.event(0, 0));
    else if (cause === "middle-button") cancelled.surface.emit("pointerdown", cancelled.event(250, 300, { button: 1 }));
    else cancelled.emitWindow(cause, cancelled.event(0, 0));
    assert.equal(cancelled.context.dialog.sketch, null, `${cause} cancels only the temporary outline`);
    assert.equal(cancelled.surface.children.length, 1);
    assert.deepEqual(detached(cancelled.context.dialog.boxes), []);
    assert.equal(cancelled.assignments.length, 0);
    assert.equal([...cancelled.listeners.values()].every((set) => set.size === 0), true);
    if (cause === "Escape") {
      assert.equal(cancelled.nodes.pageDialog.open, true, "First Escape keeps the figure editor open");
      assert.equal(cancelled.context.closeRequests || 0, 0);
      cancelled.nodes.pageDialog.emit("cancel", cancelled.event(0, 0));
      assert.equal(cancelled.context.closeRequests, 1, "A following Escape uses the normal unsaved-draft guard");
    }
  }

  const saving = harness(); saving.surface.emit("pointerdown", saving.event(150, 150));
  await saving.context.savePageCrop();
  assert.ok(saving.context.dialog.sketch, "Save cannot silently throw away an unfinished outline");
  assert.match(saving.statuses.at(-1).message, /先再点一下固定/);

  for (const mode of ["new", "regions", "read"]) {
    const legacy = harness(mode);
    legacy.surface.emit("pointerdown", legacy.event(150, 150));
    assert.equal(legacy.context.dialog.sketch, null);
    assert.equal(typeof legacy.context.dialog.drag, "function", `${mode} preserves its drag-to-create behavior`);
    legacy.emitWindow("pointerup", legacy.event(300, 400));
    assert.deepEqual(detached(legacy.context.dialog.boxes), [{ page_idx: 2, bbox: [100, 100, 400, 350] }]);
  }

  for (const [handle, end, expected] of [
    ["move", [600, 1050], [800, 800, 1000, 1000]],
    ["se", [650, 1100], [100, 100, 1000, 1000]],
    ["nw", [-100, -100], [0, 0, 300, 300]]
  ]) {
    const existing = harness(); const box = { page_idx: 2, bbox: [100, 100, 300, 300], slot: "B" };
    existing.context.dialog.boxes.push(box);
    const frame = new Node("edit-box figure"); existing.surface.frame = frame;
    existing.context.startDrag(existing.event(150, 150, { currentTarget: frame }), existing.surface, 0, handle);
    existing.emitWindow("pointermove", existing.event(...end));
    existing.emitWindow("pointerup", existing.event(...end));
    assert.deepEqual(detached(box.bbox), expected, `${handle} still edits existing figure boxes with edge limits`);
    assert.equal(box.slot, "B", "Moving/resizing does not alter option ownership");
  }

  const cancelledMove = harness(); const existingBox = { page_idx: 2, bbox: [100, 100, 300, 300], slot: "stem" };
  cancelledMove.context.dialog.boxes.push(existingBox);
  const frame = new Node("edit-box figure"); cancelledMove.surface.frame = frame;
  cancelledMove.context.startDrag(cancelledMove.event(150, 150, { currentTarget: frame }), cancelledMove.surface, 0, "move");
  cancelledMove.emitWindow("pointermove", cancelledMove.event(300, 400));
  cancelledMove.emitWindow("pointercancel", cancelledMove.event(300, 400));
  assert.deepEqual(detached(existingBox.bbox), [100, 100, 300, 300], "Cancelling a move restores the original figure range");

  // A browser double click is pointerdown/up + click twice, then dblclick.
  // The first two clicks must not leave a second rectangle or change the host.
  const double = harness();
  const template = { page_idx: 2, bbox: [100, 100, 300, 250], slot: "A", join: true,
    candidate_key: "original", label_offset: { x: 12, y: 8 } };
  double.context.dialog.boxes.push(template);
  const old = detached(template);
  for (const detail of [1, 2]) {
    double.surface.emit("pointerdown", double.event(400, 650, { detail }));
    double.emitWindow("pointerup", double.event(400, 650, { detail }));
    double.surface.emit("click", double.event(400, 650, { detail }));
  }
  double.surface.emit("dblclick", double.event(400, 650, { detail: 2 }));
  assert.equal(double.context.dialog.sketch, null);
  assert.equal(double.assignments.length, 1, "A real double-click sequence creates exactly one pending assignment");
  assert.deepEqual(double.assignments[0].target, { kind: "new", box: { page_idx: 2, bbox: [500, 525, 700, 675] } });
  assert.deepEqual(template, old, "Copying does not move or reassign the original figure");
  assert.deepEqual(detached(double.context.dialog.boxes), [old], "Copied dimensions stay temporary until ownership is chosen");
  assert.equal(double.surface.children.filter((node) => node.classList.contains("drawing")).length, 0);
  assert.equal(double.surface.children.filter((node) => node.classList.contains("pending-assignment")).length, 1);

  const nearest = harness();
  nearest.context.dialog.boxes.push(
    { page_idx: 2, bbox: [0, 0, 200, 200], slot: "A" },
    { page_idx: 2, bbox: [600, 500, 900, 600], slot: "B" },
    { page_idx: 1, bbox: [600, 500, 1000, 1000], slot: "C" }
  );
  nearest.context.dialog.selected = 0;
  nearest.surface.emit("dblclick", nearest.event(570, 650));
  assert.deepEqual(nearest.assignments[0].target.box.bbox, [700, 550, 1000, 650], "Use the nearest frame on this page, shifting inward at its edge without shrinking");
  assert.equal(nearest.assignments[0].target.box.bbox[2] - nearest.assignments[0].target.box.bbox[0], 300);
  assert.equal(nearest.assignments[0].target.box.bbox[3] - nearest.assignments[0].target.box.bbox[1], 100);

  const otherPage = harness();
  otherPage.context.dialog.boxes.push({ page_idx: 0, bbox: [100, 200, 250, 500], slot: "B" },
    { page_idx: 1, bbox: [300, 200, 600, 400], slot: "D" });
  otherPage.context.dialog.selected = 0;
  otherPage.surface.emit("dblclick", otherPage.event(100, 50));
  assert.deepEqual(otherPage.assignments[0].target.box.bbox, [0, 0, 150, 300], "Without a same-page frame, use the last selected dimensions and keep them at page edges");
  otherPage.context.dialog.selected = null;
  otherPage.surface.emit("dblclick", otherPage.event(350, 550));
  assert.deepEqual(otherPage.assignments[1].target.box.bbox, [350, 400, 650, 600], "Without a selected frame, reuse the most recently added figure's dimensions");
  assert.equal(otherPage.surface.children.filter((node) => node.classList.contains("pending-assignment")).length, 1, "Repeating a double click replaces only an unfinished pending assignment");

  const jitter = harness(); jitter.context.dialog.boxes.push({ page_idx: 2, bbox: [0, 0, 200, 200], slot: "A" });
  jitter.surface.emit("pointerdown", jitter.event(300, 350));
  jitter.emitWindow("pointerup", jitter.event(300, 350));
  jitter.surface.emit("click", jitter.event(300, 350, { detail: 1 }));
  jitter.surface.emit("pointerdown", jitter.event(305, 360));
  jitter.emitWindow("pointerup", jitter.event(305, 360));
  jitter.surface.emit("click", jitter.event(305, 360, { detail: 2 }));
  assert.equal(jitter.assignments.length, 1, "Mouse jitter can temporarily finish a two-point outline");
  jitter.surface.emit("dblclick", jitter.event(305, 360, { detail: 2 }));
  assert.deepEqual(jitter.assignments.at(-1).target.box.bbox, [310, 210, 510, 410]);
  assert.equal(jitter.surface.children.filter((node) => node.classList.contains("pending-assignment")).length, 1, "The subsequent double click removes that outline and leaves exactly one same-size frame");
  assert.equal(jitter.context.dialog.boxes.length, 1);

  for (const boxes of [[], [{ page_idx: 2, bbox: [-100, 0, 1100, 300], slot: "A" }]]) {
    const impossible = harness(); impossible.context.dialog.boxes.push(...boxes);
    impossible.surface.emit("pointerdown", impossible.event(300, 350));
    impossible.surface.emit("dblclick", impossible.event(300, 350));
    assert.equal(impossible.context.dialog.sketch, null);
    assert.equal(impossible.assignments.length, 0);
    assert.equal(impossible.statuses.at(-1).error, true, "No template or oversized dimensions give an explicit instruction");
  }

  const unmoved = harness(); unmoved.context.dialog.boxes.push({ page_idx: 2, bbox: [100, 100, 300, 300], slot: "A" });
  const originalFrame = new Node("edit-box figure"); unmoved.surface.frame = originalFrame;
  unmoved.context.startDrag(unmoved.event(150, 150, { currentTarget: originalFrame }), unmoved.surface, 0, "move");
  unmoved.emitWindow("pointerup", unmoved.event(150, 150));
  assert.equal(unmoved.context.renders || 0, 0, "An unmoved existing frame stays connected for the browser's following double click");

  const touch = harness(); touch.surface.emit("pointerdown", touch.event(150, 150, { pointerType: "touch", pointerId: 8 }));
  touch.emitWindow("pointerup", touch.event(150, 150, { pointerType: "touch", pointerId: 8 }));
  touch.surface.emit("pointerdown", touch.event(300, 400, { pointerType: "touch", pointerId: 9 }));
  assert.equal(touch.assignments.length, 1, "Two taps may have different pointer IDs");

  console.log("Two-click figures: released-mouse preview, double-click dimension copying, browser click sequences, ownership, edge/zoom geometry, cancellation, save protection and existing crop controls: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
