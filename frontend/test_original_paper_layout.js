"use strict";
const assert = require("node:assert/strict");
const L = require("./original-paper-layout.js");
const q = (id, number, group, ranges, extra = {}) => ({ id, number, group: { id: group, title: `组${group}` }, question_type: "free_response", regions: ranges, content_revision: id, layout_fingerprint: String(id).padStart(64, "0"), color_index: id % 6, ...extra });
const r = (page, bbox) => ({ page_idx: page, bbox });
const a = q(1, 1, 10, [r(0, [10, 10, 900, 900]), r(1, [10, 10, 800, 200])], { edited_stem: "人工文字" });
const b = q(2, 2, 10, [r(0, [20, 20, 300, 200])]);
const c = q(3, 1, 20, [r(0, [40, 40, 100, 100])]);
const pages = [{ page_idx: 0 }, { page_idx: 1 }];
const questions = [a, b, c];
assert.deepEqual(L.hitTest(questions, 0, [60, 60]).map((h) => h.question.id), [3, 2, 1], "overlap chooses smallest ranges first, including a large enclosing range");
assert.equal(L.hitTest(questions, 1, [60, 60]).length, 1);
assert.equal(L.colorOf(a), L.colorOf({ ...a, regions: [a.regions[1]] }), "cross-page pieces share the persisted color");
assert.equal(L.COLORS.length, 6);
const supplement = L.createDraft("add", [a], questions, 20);
assert.deepEqual(supplement.sources, [], "supplement never treats a previously selected question as a source");
assert.equal(supplement.targets[0].group_id, 20);
assert.equal(supplement.targets[0].number, 2);
let split = L.createDraft("split", [a], questions);
assert.deepEqual(split.targets[0].regions, a.regions, "first split target inherits all ranges");
assert.deepEqual(split.targets[1].regions, [], "other split target is blank, never duplicates source ranges");
assert.equal(split.targets[1].number, 3);
assert.match(L.validate(split, questions, pages), /空题/);
split.targets[1].regions.push(split.targets[0].regions.pop());
assert.equal(L.validate(split, questions, pages), "");
assert.equal(L.resizeSplit(split, 12, questions).targets.length, 12);
assert.throws(() => L.resizeSplit(split, 13, questions), /2–12/);
assert.throws(() => L.createDraft("merge", [a, c], questions), /同一题组/);
const merged = L.createDraft("merge", [a, b], questions);
assert.deepEqual(merged.targets[0].regions.map((x) => x.page_idx), [0, 1, 0], "merge retains explicit source and piece order across pages");
assert.deepEqual(a.regions[0].bbox, [10, 10, 900, 900], "draft edits never change canonical source objects");
assert.equal(L.validate({ ...merged, targets: [{ ...merged.targets[0], number: 1, group_id: 20 }] }, questions, pages), "这个题组中已存在该题号，请调整题号");
assert.throws(() => L.createDraft("merge", [a, q(4, 4, 10, Array.from({ length: 11 }, () => r(0, [1, 1, 2, 2])))], questions), /12 段/);
const body = L.payload(L.createDraft("regions", [a], questions), 9, "request");
assert.deepEqual(Object.keys(body.targets[0]), ["regions"], "region-only payload cannot rewrite manual content or metadata");
assert.equal(body.sources[0].fingerprint, a.layout_fingerprint);
assert.equal(body.layout_revision, 9);
assert.equal(JSON.stringify(body).includes("人工文字"), false);
const renumber = L.createDraft("renumber", [a], questions);
renumber.targets[0].number = 14;
assert.deepEqual(L.payload(renumber, 9, "renumber").targets, [{ number: 14 }], "renumber submits only the new number, never ranges or text");
assert.equal(L.validate(renumber, questions, pages), "");
renumber.targets[0].number = 2;
assert.match(L.validate(renumber, questions, pages), /已存在该题号/);
renumber.targets[0].number = 0;
assert.match(L.validate(renumber, questions, pages), /1–999/);
renumber.targets[0].number = 1;
assert.equal(L.validate(renumber, questions, pages), "", "same number on the source or another group is allowed");
assert.equal(L.movedEnough({ clientX: 10, clientY: 10 }, { clientX: 13, clientY: 13 }), false, "three-pixel diagonal jitter is harmless");
assert.equal(L.movedEnough({ clientX: 10, clientY: 10 }, { clientX: 14, clientY: 10 }), true);
assert.deepEqual(L.moveBox([800, 850, 999, 998], 20, 20), [801, 852, 1000, 1000]);
const evidence = L.sourceEvidence({ questions: [a, b], region_reads: [{ id: 4, question_id: 1, text: "旧识读" }, { id: 5, question_id: 2 }], publications: [{ id: 8, question_id: 1 }, { id: 9, question_id: 2 }], library_jobs: [{ id: 11, publication_id: 8, result: { answer: "旧答案" } }, { id: 12, publication_id: 9 }] }, 1);
assert.equal(evidence.question.edited_stem, "人工文字");
assert.deepEqual(evidence.region_reads.map((item) => item.id), [4]);
assert.deepEqual(evidence.library_jobs.map((item) => item.id), [11], "historical evidence stays attached to its original source question");

// A small DOM harness drives the controller's real pointer/draft/save handlers.
class Node {
  constructor(tag = "div", cls = "", text = "") {
    this.tagName = tag.toUpperCase(); this.className = cls; this.textContent = text; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = new Map(); this.hidden = false; this.disabled = false; this.isConnected = true;
    this.style = { setProperty: (key, value) => { this.style[key] = value; } };
    this.classList = { add: (...xs) => xs.forEach((x) => this.classList.toggle(x, true)), remove: (...xs) => xs.forEach((x) => this.classList.toggle(x, false)), contains: (x) => this.className.split(" ").includes(x), toggle: (x, value) => { const s = new Set(this.className.split(" ").filter(Boolean)); const yes = value === undefined ? !s.has(x) : value; if (yes) s.add(x); else s.delete(x); this.className = [...s].join(" "); return yes; } };
  }
  append(...nodes) { nodes.forEach((n) => { if (typeof n === "string") n = new Node("text", "", n); n.parent = this; this.children.push(n); }); }
  replaceChildren(...nodes) { this.children.forEach((n) => { n.parent = null; }); this.children = []; this.append(...nodes); }
  addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(fn); }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  async emit(type, event = {}) { const e = { target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...event }; for (const fn of [...(this.listeners.get(type) || [])]) await fn(e); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return selector.slice(1).split(".").every((x) => this.classList.contains(x));
    const data = selector.match(/^\[data-([a-z-]+)(?:="([^"]*)")?\]$/);
    if (data) { const key = data[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase()); return key in this.dataset && (data[2] === undefined || String(this.dataset[key]) === data[2]); }
    return this.tagName === selector.toUpperCase();
  }
  closest(selector) { return selector.split(",").some((s) => this.matches(s.trim())) ? this : this.parent?.closest(selector) || null; }
  querySelectorAll(selector) { const out = []; const walk = (n) => n.children.forEach((child) => { if (selector.split(",").some((s) => child.matches(s.trim()))) out.push(child); walk(child); }); walk(this); return out; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((n) => n !== this); this.isConnected = false; }
  showModal() { this.open = true; Node.lastModal = this; this.querySelector("button")?.emit("click"); }
  close() { this.open = false; }
  prepend(...nodes) { const old = [...this.children]; this.replaceChildren(...nodes, ...old); }
  focus() {} scrollIntoView() {} scrollTo({ top = 0, left = 0 } = {}) { this.scrollTop = top; this.scrollLeft = left; } setPointerCapture() {} releasePointerCapture() {}
}
async function run() {
  global.window = new Node(); global.crypto ||= require("node:crypto").webcrypto;
  global.document = { querySelector: () => null, body: new Node("body") };
  global.requestAnimationFrame = (callback) => callback();
  const nodes = new Map(); const $ = (id) => { if (!nodes.has(id)) { const node = new Node(); node.id = id; nodes.set(id, node); } return nodes.get(id); };
  const dialog = { mode: "view", page: 0, imageReady: true, saving: false, tool: "select", spacePan: false };
  const state = { paperId: 7, paper: { id: 7, layout_revision: 9, pages, question_groups: [{ id: 10, title: "组10" }, { id: 20, title: "组20" }] }, questions: JSON.parse(JSON.stringify(questions)) };
  const writes = [], messages = []; let controller, serverConflict = false, serverOccupiedNumber = false, serverLoseResponse = false, lookupError = false, receipt = null, surface, nextQuestionId = 100, completed = 0;
  let historyData = { operations: [], latest_operation: null }, delayedHistory = null;
  const host = { dialog, state, $, el: (tag, cls, text) => new Node(tag, cls, text),
    button: (text, cls, fn) => { const n = new Node("button", cls, text); n.addEventListener("click", fn); return n; },
    api: async (path, options = {}) => {
      if (options.method === "POST") {
        writes.push({ path, body: options.body });
        if (serverOccupiedNumber) { const e = new Error("结果题号已被当前题卡或普通回收站题目占用。"); e.status = 409; throw e; }
        if (serverConflict) { const e = new Error("版本冲突"); e.status = 409; throw e; }
        const request = options.body;
        if (request.kind === "add") {
          const target = request.targets[0], id = nextQuestionId++;
          const added = { id, number: target.number, group_id: target.group_id, group: state.paper.question_groups.find((g) => g.id === target.group_id),
            question_type: target.question_type, regions: target.regions, body_mode: "source_image", processing_mode: "manual",
            content_revision: 1, layout_fingerprint: "f".repeat(64), color_index: id % 6 };
          const result = { paper: { ...state.paper, layout_revision: state.paper.layout_revision + 1 }, questions: [...state.questions, added], operation: { id: id + 500, target_ids: [id], kind: "add" } };
          if (serverLoseResponse) { receipt = result; throw new TypeError("connection lost after commit"); }
          return result;
        }
        const updated = state.questions.map((item) => item.id === request.sources[0]?.id ? { ...item, ...(request.kind === "renumber" ? { number: request.targets[0].number } : { regions: request.targets[0].regions }), content_revision: item.content_revision + 1 } : item);
        const result = { paper: { ...state.paper, layout_revision: state.paper.layout_revision + 1 }, questions: updated, operation: { id: 50, target_ids: [request.sources[0]?.id], kind: request.kind } };
        if (serverLoseResponse) { receipt = result; throw new TypeError("connection lost after commit"); }
        return result;
      }
      if (path.includes("client_request_id=")) { if (lookupError) throw new TypeError("lookup disconnected"); return receipt || { operation: null }; }
      return delayedHistory || { ...historyData, operation: null, layout_revision: state.paper.layout_revision };
    },
    toast: (message) => messages.push(message), cropView: () => new Node(), renderStage: () => { controller?.renderParts(); surface = new Node("div", "stage-surface"); controller?.renderSurface(surface); }, renderPageTabs() {},
    goToDialogPage: (page) => { dialog.page = page; }, pointFrom: (e) => [e.clientX, e.clientY], placeBox: (node, bbox) => { node.bbox = [...bbox]; },
    setCropSaving: (saving) => { dialog.saving = saving; }, trackCropDraft() {}, confirmDialog: async () => true,
    TYPE_NAMES: { unknown: "未定", free_response: "解答题" }, questionCompare: (x, y) => x.id - y.id, rereadQuestion: () => { throw Error("Layout must never call recognition"); },
    isApproved: (item) => Boolean(item.approved), applyCanonical: (data) => { state.paper = data.paper; state.questions = data.questions; }, reloadCanonical: async () => {},
    returnReview: async () => { completed += 1; }, completeManualCut: async () => { completed += 1; }
  };
  controller = L.createController(host); controller.open("regions", state.questions[0]); host.renderStage();
  const snapshot = () => JSON.parse(controller.snapshot().layout);
  const pointer = (x, y, id = 11, target = surface) => ({ clientX: x, clientY: y, pointerId: id, button: 0, target });
  const labelNode = () => surface.querySelector('[data-layout-drag-label="0"]');
  const original = snapshot().targets[0].regions[0].bbox;
  await labelNode().emit("pointerdown", pointer(10, 10));
  await window.emit("pointermove", pointer(13, 13)); await window.emit("pointerup", pointer(13, 13));
  assert.deepEqual(snapshot().targets[0].regions[0].bbox, original, "label jitter creates no geometric edit or undo entry");
  assert.equal(dialog.history.length, 0);
  await labelNode().emit("pointerdown", pointer(10, 10));
  await window.emit("pointermove", pointer(30, 30, 99));
  assert.deepEqual(snapshot().targets[0].regions[0].bbox, original, "unrelated pointer cannot drag");
  await window.emit("pointermove", pointer(14, 10)); await window.emit("pointerup", pointer(14, 10));
  assert.equal(snapshot().targets[0].regions[0].bbox[0], 14); assert.equal(dialog.history.length, 1);
  dialog.page = 1; controller.restore(false); assert.deepEqual(snapshot().targets[0].regions[0].bbox, original); assert.equal(dialog.page, 1, "cross-page undo keeps current page");
  controller.restore(true); assert.equal(snapshot().targets[0].regions[0].bbox[0], 14); assert.equal(dialog.page, 1);
  dialog.page = 0; host.renderStage();
  // Select the first part, then cancel a moved gesture and prove no new history.
  await surface.emit("pointerdown", pointer(850, 850)); host.renderStage();
  await labelNode().emit("pointerdown", pointer(14, 10)); await window.emit("pointermove", pointer(20, 10));
  await window.emit("pointercancel", pointer(20, 10));
  assert.equal(snapshot().targets[0].regions[0].bbox[0], 14);
  assert.equal(dialog.history.length, 1, "cancelled geometry never enters undo history");
  await controller.preview();
  assert.equal(writes.length, 1); assert.equal(writes[0].path, "/api/papers/7/question-layout");
  assert.deepEqual(Object.keys(writes[0].body.targets[0]), ["regions"]);
  assert.equal(dialog.page, 0); assert.equal(dialog.mode, "regions", "saved question remains selected for direct range adjustment");
  assert.equal(state.questions[0].edited_stem, "人工文字", "manual text survives canonical save");
  assert.equal(controller.dirty(), false, "canonical save leaves a clean selected frame");
  await controller.begin("split"); assert.equal(snapshot().targets.length, 2);
  await $("pageLayoutPanel").querySelector('[data-layout-target-index="1"]').emit("click");
  assert.equal(dialog.boxes.length, 0, "target selection keeps distinct range lists");
  dialog.page = 1; await $("pageLayoutPanel").querySelector("#pageLayoutDraw").emit("click"); host.renderStage();
  await surface.emit("pointerdown", pointer(100, 300)); await surface.emit("pointerdown", pointer(500, 700));
  assert.equal(snapshot().targets[1].regions[0].page_idx, 1); controller.restore(false); assert.equal(snapshot().targets[1].regions.length, 0); controller.restore(true);
  // Start a fresh region draft and verify 409 retains draft/revision for review.
  controller.close(); dialog.page = 0; controller.open("regions", state.questions[0]); host.renderStage();
  await labelNode().emit("pointerdown", pointer(14, 10)); await window.emit("pointermove", pointer(18, 10)); await window.emit("pointerup", pointer(18, 10));
  const revision = state.paper.layout_revision; state.paper.layout_revision = 100; serverConflict = true; await controller.preview();
  assert.equal(writes.at(-1).body.layout_revision, revision, "draft never silently rebases to a newer paper revision");
  assert.equal(snapshot().targets[0].regions[0].bbox[0], 18, "failed save retains full draft");
  assert.ok($("pageLayoutPanel").querySelector("#pageLayoutConflict"));
  assert.ok(messages.some((m) => /保存被阻止/.test(m)));
  controller.close(); state.paper.layout_revision = revision; controller.open("regions", state.questions[0]); host.renderStage();
  await labelNode().emit("pointerdown", pointer(14, 10)); await window.emit("pointermove", pointer(18, 10));
  const esc = { key: "Escape", target: surface, preventDefault() { this.prevented = true; }, stopPropagation() {} };
  assert.equal(controller.keydown(esc), true); assert.equal(esc.prevented, true);
  assert.equal(snapshot().targets[0].regions[0].bbox[0], 14, "Escape restores the active gesture without closing the draft");
  await labelNode().emit("pointerdown", pointer(14, 10)); await window.emit("pointermove", pointer(18, 10)); await window.emit("pointerup", pointer(18, 10));
  serverConflict = false; serverLoseResponse = true; lookupError = true; const beforeWrites = writes.length; await controller.preview();
  assert.equal(writes.length, beforeWrites + 1); assert.equal(controller.hasUnknownSave(), true);
  await controller.begin("add"); assert.equal(snapshot().kind, "regions", "unknown save blocks switching and duplicate writes");
  lookupError = false; await $("pageLayoutPanel").querySelector("#pageLayoutRetryLookup").emit("click");
  assert.equal(writes.length, beforeWrites + 1, "receipt recovery never repeats the layout POST");
  assert.equal(controller.hasUnknownSave(), false); assert.equal(controller.dirty(), false);
  const valid = { id: 50, kind: "regions", can_undo: true, created_at: "2026-10-09T00:00:00Z" };
  historyData = { operations: [{ id: 51, kind: "regions", can_undo: false, created_at: "2026-10-09T00:01:00Z", blocked_reason: "NO_CHANGE" }, valid], latest_operation: valid };
  controller.close(); controller.open("view", state.questions[0]); await Promise.resolve(); await Promise.resolve();
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutUndoSaved").disabled, false, "no-op receipt never replaces the latest undoable saved operation");
  dialog.page = 1;
  await controller.selectQuestion(3, null);
  assert.equal(dialog.page, 0, "question-list selection locates the question's first page");
  await controller.selectQuestion(1, null);
  serverLoseResponse = false;
  const beforeRenumber = JSON.parse(JSON.stringify(state.questions[0]));
  await $("pageLayoutPanel").querySelector("#pageLayoutRenumber").emit("click");
  assert.equal(snapshot().kind, "renumber");
  assert.equal($("pageDialogSave").textContent, "保存");
  assert.equal(surface.querySelector("[data-layout-handle]"), null);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDraw"), null);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutSplitCount"), null);
  controller.remove(0);
  assert.deepEqual(snapshot().targets[0].regions, beforeRenumber.regions, "Delete cannot change a range during renumber");
  const beforeNumberWrites = writes.length;
  await controller.preview();
  assert.equal(writes.length, beforeNumberWrites, "unchanged number is not saved");
  const numberInput = $("pageLayoutPanel").querySelector("#pageLayoutNumber");
  numberInput.value = "2"; await numberInput.emit("input");
  await controller.preview();
  assert.equal(writes.length, beforeNumberWrites, "duplicate number keeps draft without a request");
  numberInput.value = "14"; await numberInput.emit("input");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutNumber"), numberInput, "typing keeps the field and save button mounted");
  serverOccupiedNumber = true;
  await controller.preview();
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutNumber").value, 14, "server-side trash conflict keeps the entered number");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutConflict"), null, "an occupied number is not a stale-layout conflict");
  serverOccupiedNumber = false;
  await controller.preview();
  assert.equal(writes.length, beforeNumberWrites + 2, "renumber saves directly without a second confirmation after resolving occupation");
  assert.notEqual(writes.at(-1).body.client_request_id, writes.at(-2).body.client_request_id, "rejected occupation releases its request ID for a fresh save");
  assert.deepEqual(writes.at(-1).body.targets, [{ number: 14 }]);
  assert.equal(state.questions[0].number, 14);
  assert.deepEqual(state.questions[0].regions, beforeRenumber.regions);
  assert.equal(state.questions[0].color_index, beforeRenumber.color_index);
  assert.equal(state.questions[0].edited_stem, beforeRenumber.edited_stem);
  assert.equal(controller.dirty(), false);
  await $("pageLayoutPanel").querySelector("#pageLayoutAdd").emit("click");
  assert.equal(snapshot().kind, "add");
  assert.deepEqual(snapshot().sources, [], "clicking supplement after selecting a question submits no source rows");
  assert.equal(snapshot().targets[0].group_id, 10, "supplement starts in the previously selected question group");
  controller.close();
  let releaseHistory;
  delayedHistory = new Promise((resolve) => { releaseHistory = resolve; });
  controller.open("view", state.questions[0]);
  await controller.begin("renumber");
  const focusedNumber = $("pageLayoutPanel").querySelector("#pageLayoutNumber");
  focusedNumber.value = "15"; await focusedNumber.emit("input");
  releaseHistory({ ...historyData, layout_revision: state.paper.layout_revision });
  await Promise.resolve(); await Promise.resolve();
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutNumber"), focusedNumber, "late history response never remounts the renumber input");
  assert.equal(focusedNumber.value, "15");
  delayedHistory = null;
  controller.close(); state.paper.archived = true;
  controller.open("regions", state.questions[0]);
  assert.equal(controller.snapshot().layout, "", "archived originals open for inspection without an editable draft");
  for (const id of ["pageLayoutAdd", "pageLayoutRenumber", "pageLayoutSplit", "pageLayoutMerge", "pageLayoutUndoSaved"]) {
    assert.equal($("pageLayoutPanel").querySelector(`#${id}`).disabled, true, `${id} remains read-only while archived`);
  }
  await controller.begin("add");
  assert.equal(controller.snapshot().layout, "", "programmatic action cannot bypass archived read-only state");

  // The unified workspace opens a continuous add draft, saves through the
  // layout service, and advances only after the server confirms the receipt.
  state.paper.archived = false; dialog.page = 1; dialog.zoom = 0.73;
  controller.close(); controller.open("view", null); await Promise.resolve(); await Promise.resolve();
  await $("pageLayoutPanel").querySelector("#pageLayoutAdd").emit("click");
  assert.equal(snapshot().kind, "add"); assert.equal(dialog.tool, "draw", "new question starts ready for two-point framing");
  assert.equal($("pageDialogSave").textContent, "保存");
  assert.equal($("pageDialogComplete").hidden, false);
  assert.ok($("pageLayoutPanel").querySelector("#pageLayoutTargetPreview"), "crop preview is always visible while adding");
  const input = () => $("pageLayoutPanel").querySelector("#pageLayoutNumber");
  input().value = "4"; await input().emit("change");
  const type = $("pageLayoutPanel").querySelector("#pageLayoutType"); type.value = "single_choice"; await type.emit("change");
  dialog.page = 1; $("pageStage").scrollTop = 77; $("pageStage").scrollLeft = 9;
  const beforeControlShortcut = writes.length;
  const ignoredControlShortcut = { key: "s", target: new Node("button"), ctrlKey: true, metaKey: false, altKey: false,
    repeat: false, isComposing: false, keyCode: 83, defaultPrevented: false, preventDefault() {}, stopPropagation() {} };
  assert.equal(controller.keydown(ignoredControlShortcut), false, "Ctrl+S on guide/toolbar controls does not save or complete");
  assert.equal(writes.length, beforeControlShortcut);
  const drawRegion = async (x = 100) => {
    const canvas = surface;
    await canvas.emit("pointerdown", pointer(x, 100));
    await canvas.emit("pointerdown", pointer(x + 200, 400));
  };
  const canvasKey = (key, ctrlKey = false, shiftKey = false) => {
    const target = new Node("div"), closest = target.closest.bind(target);
    target.closest = (selector) => selector === "#pageStage" ? surface : closest(selector);
    return { key, target, ctrlKey, shiftKey, metaKey: false, altKey: false, repeat: false, isComposing: false,
      keyCode: key === "Enter" ? 13 : 83, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
  };
  const nextEventLoop = () => new Promise((resolve) => setTimeout(resolve, 0));
  await drawRegion();
  const beforeFirstAdd = writes.length;
  assert.equal(controller.keydown(canvasKey("Enter", false, true)), true, "Shift+Enter on the canvas saves and opens the next draft");
  await nextEventLoop();
  assert.equal(writes.length, beforeFirstAdd + 1);
  assert.equal(writes.at(-1).body.kind, "add"); assert.equal(writes.at(-1).body.targets[0].number, 4);
  assert.equal(dialog.page, 1); assert.equal(dialog.zoom, 0.73); assert.equal($("pageStage").scrollTop, 77); assert.equal($("pageStage").scrollLeft, 9);
  assert.equal(snapshot().kind, "add"); assert.equal(snapshot().targets[0].number, 5, "next number skips occupied active numbers after the saved question");

  // A committed request with a lost response is looked up by request ID. It
  // advances once and never repeats the POST.
  await drawRegion(350); serverLoseResponse = true; lookupError = true;
  const beforeLost = writes.length; assert.equal(controller.keydown(canvasKey("Enter", false, true)), true); await nextEventLoop();
  assert.equal(writes.length, beforeLost + 1); assert.equal(controller.hasUnknownSave(), true);
  lookupError = false;
  await $("pageLayoutPanel").querySelector("#pageLayoutRetryLookup").emit("click");
  serverLoseResponse = false;
  assert.equal(writes.length, beforeLost + 1, "receipt recovery never repeats the add request");
  assert.equal(controller.hasUnknownSave(), false); assert.equal(snapshot().kind, "add");
  assert.equal(snapshot().targets[0].number, 6);

  const beforeEmptyFinish = writes.length;
  assert.equal(controller.keydown(canvasKey("Enter", true)), true, "Ctrl+Enter returns from an empty next draft without creating a question"); await nextEventLoop();
  assert.equal(writes.length, beforeEmptyFinish, "finishing an empty next-question draft does not create a question");
  assert.equal(completed, 1);

  controller.close(); controller.open("view", null); await Promise.resolve(); await Promise.resolve();
  await $("pageLayoutPanel").querySelector("#pageLayoutAdd").emit("click");
  await drawRegion(500);
  const beforeLast = writes.length;
  assert.equal(controller.keydown(canvasKey("Enter", true)), true, "Ctrl+Enter offers to save a valid final crop and return"); await nextEventLoop();
  assert.equal(writes.length, beforeLast + 1, "complete saves the final valid new question before leaving");
  assert.equal(completed, 2);

  // Removing a frame is still a draft range edit, never an implicit deletion
  // of the saved question. Drive the controller's empty-state actions through
  // their actual mounted buttons so recovery preserves prior adjustments.
  controller.close();
  const single = q(200, 40, 10, [r(0, [100, 100, 400, 400])], { edited_stem: "保留的人工题文" });
  const multiple = q(201, 41, 10, [r(0, [500, 100, 700, 400]), r(1, [100, 500, 400, 800])]);
  const published = q(202, 42, 10, [r(0, [500, 500, 700, 800])], { publication: { id: 902, version: 1 } });
  state.questions = JSON.parse(JSON.stringify([single, multiple, published]));
  state.paper.archived = false; dialog.page = 0;
  const deleteCalls = []; let deleteResult = false, deleteFailure = false, resolveDeletion = null, pendingDeletion = null;
  host.questionDeleteBlockReason = (item) => item.publication ? "已入库题目请先撤回入库版本，再删除。" : "";
  host.deleteQuestion = async (item) => {
    deleteCalls.push(item.id);
    if (deleteFailure) { messages.push("删除失败，题目未改变"); return false; }
    const result = pendingDeletion ? await pendingDeletion : deleteResult;
    if (result) state.questions = state.questions.filter((question) => question.id !== item.id);
    return result;
  };
  const sidebarText = (node = $("pageLayoutPanel")) => [node.textContent, ...node.children.map((child) => sidebarText(child))].join(" ");
  const openRegions = (item) => { controller.close(); controller.open("regions", item); host.renderStage(); };
  const removeMountedPart = async (index) => {
    const row = $("pageLayoutPanel").querySelector(`[data-layout-part-index="${index}"]`);
    assert.ok(row, "the range to remove is visible in the sidebar");
    await row.querySelectorAll("button").find((node) => node.textContent === "移除").emit("click");
  };

  openRegions(state.questions[0]);
  await labelNode().emit("pointerdown", pointer(100, 100));
  await window.emit("pointermove", pointer(108, 100)); await window.emit("pointerup", pointer(108, 100));
  const adjusted = snapshot().targets[0].regions;
  assert.deepEqual(adjusted[0].bbox, [108, 100, 408, 400]);
  await removeMountedPart(0);
  const emptyDraft = controller.snapshot().layout, beforeEmptySave = writes.length;
  assert.equal(snapshot().targets[0].regions.length, 0);
  assert.equal(controller.dirty(), true, "removing the sole frame remains an unsaved range change");
  assert.ok($("pageLayoutPanel").querySelector("#pageLayoutEmptyRange"));
  assert.match(sidebarText(), /这道题已经没有范围框/);
  assert.match(sidebarText(), /题目尚未删除/);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutRestoreFrame").textContent, "恢复刚删的框");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDraw").textContent, "重新画框");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion").textContent, "删除这题");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutParts"), null, "empty recovery replaces misleading empty part controls");
  assert.doesNotMatch(sidebarText(), /结果 1|0 段|1 个框|添加范围片段/, "empty state does not repeat stale saved counts or internal result terminology");
  assert.equal($("pageDialogSave").disabled, true, "zero-range drafts visibly disable ordinary save");
  await controller.preview();
  assert.equal(writes.length, beforeEmptySave, "programmatic save also refuses a zero-range request");
  assert.deepEqual(state.questions[0].regions, single.regions, "draft frame removal never changes the canonical question");
  assert.equal(await controller.returnToReview(), false, "an invalid draft offers to continue instead of attempting save-and-leave");
  assert.deepEqual(Node.lastModal.querySelectorAll("button").map((node) => node.textContent), ["继续修改", "放弃修改"]);
  assert.equal(controller.snapshot().layout, emptyDraft); assert.equal(completed, 2);
  await $("pageLayoutPanel").querySelector("#pageLayoutRestoreFrame").emit("click");
  assert.deepEqual(snapshot().targets[0].regions, adjusted, "restoring the removed frame keeps the preceding size or position adjustment");
  assert.equal(dialog.history.length, 1, "restore only reverses the removal, retaining the earlier adjustment's undo entry");
  assert.equal(controller.dirty(), true); assert.equal($("pageDialogSave").disabled, false);

  await removeMountedPart(0);
  await $("pageLayoutPanel").querySelector("#pageLayoutDraw").emit("click");
  assert.equal(dialog.tool, "draw"); assert.equal($("pageStage").classList.contains("layout-drawing"), true);
  await surface.emit("pointerdown", pointer(150, 200));
  assert.ok(dialog.sketch, "the first corner starts a cancellable frame sketch");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDraw").attributes["aria-pressed"], "true");
  assert.equal(controller.keydown({ key: "Escape", target: surface, preventDefault() {}, stopPropagation() {} }), true);
  assert.equal(dialog.sketch, null); assert.equal(dialog.tool, "select");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDraw").textContent, "重新画框");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDraw").attributes["aria-pressed"], "false", "Escape updates the mounted draw control instead of leaving a stale cancel action");
  assert.equal(snapshot().targets[0].regions.length, 0);
  await $("pageLayoutPanel").querySelector("#pageLayoutDraw").emit("click");
  assert.equal(dialog.tool, "draw", "one click restarts drawing after Escape");
  await surface.emit("pointerdown", pointer(150, 200)); await surface.emit("pointerdown", pointer(450, 600));
  assert.deepEqual(snapshot().targets[0].regions, [r(0, [150, 200, 450, 600])]);
  assert.equal(dialog.tool, "select", "a fixed replacement frame exits drawing and is directly adjustable");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutEmptyRange"), null);
  assert.equal($("pageDialogSave").disabled, false);
  const beforeReplacementSave = writes.length; await controller.preview();
  assert.equal(writes.length, beforeReplacementSave + 1);
  assert.deepEqual(writes.at(-1).body.targets, [{ regions: [r(0, [150, 200, 450, 600])] }]);
  assert.equal(state.questions[0].edited_stem, single.edited_stem);
  assert.equal(controller.dirty(), false, "saving the redrawn frame keeps the saved question selected and clean");
  assert.equal(dialog.question.id, single.id); assert.equal(snapshot().sources[0].id, single.id);

  openRegions(state.questions.find((item) => item.id === multiple.id));
  await removeMountedPart(0);
  assert.match(sidebarText(), /1 个框 · 第 2 页/, "remaining frame count and page come from the current draft");
  assert.doesNotMatch(sidebarText(), /2 个框|结果 1/);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion"), null, "a remaining frame uses the ordinary range-edit interface");
  assert.deepEqual(snapshot().targets[0].regions, [multiple.regions[1]]);
  await removeMountedPart(0);
  const beforeCancelledDeletion = controller.snapshot().layout, beforeCancelledHistory = JSON.stringify(dialog.history);
  await $("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion").emit("click");
  assert.deepEqual(deleteCalls, [multiple.id]);
  assert.equal(controller.snapshot().layout, beforeCancelledDeletion, "cancelled question deletion keeps the complete range draft");
  assert.equal(JSON.stringify(dialog.history), beforeCancelledHistory);
  assert.equal(dialog.question.id, multiple.id); assert.equal(dialog.saving, false);
  deleteFailure = true;
  await $("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion").emit("click");
  assert.equal(deleteCalls.length, 2);
  assert.equal(controller.snapshot().layout, beforeCancelledDeletion, "a host-reported deletion failure also preserves the draft and selection");
  assert.equal(state.questions.some((item) => item.id === multiple.id), true);
  assert.equal(messages.at(-1), "删除失败，题目未改变"); deleteFailure = false;

  pendingDeletion = new Promise((resolve) => { resolveDeletion = resolve; });
  const deleteButton = $("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion");
  const deleting = deleteButton.emit("click");
  assert.equal(dialog.saving, true, "a confirmed deletion is busy until the host resolves");
  await deleteButton.emit("click"); await controller.preview(); controller.remove(0);
  assert.equal(deleteCalls.length, 3, "repeated clicks cannot submit a second question deletion while busy");
  assert.equal(controller.snapshot().layout, beforeCancelledDeletion, "busy state preserves the draft while the result is unknown");
  resolveDeletion(true); await deleting; pendingDeletion = null;
  assert.equal(state.questions.some((item) => item.id === multiple.id), false);
  assert.equal(controller.snapshot().layout, "", "only host-confirmed successful deletion clears the draft");
  assert.equal(dialog.question, null, "successful deletion also clears the selected source question");
  assert.equal(dialog.saving, false); assert.equal($("pageLayoutPanel").querySelector("#pageLayoutEmptyRange"), null);

  openRegions(state.questions.find((item) => item.id === published.id));
  await removeMountedPart(0);
  const blockedDelete = $("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion"), beforeBlockedCalls = deleteCalls.length;
  assert.equal(blockedDelete.disabled, true); assert.match(blockedDelete.title, /已入库/);
  assert.match(sidebarText(), /已入库题目请先撤回入库版本/);
  await blockedDelete.emit("click");
  assert.equal(deleteCalls.length, beforeBlockedCalls, "the handler rechecks publication protection even if a disabled button is invoked");
  assert.equal(snapshot().targets[0].regions.length, 0); assert.equal(dialog.question.id, published.id);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutRestoreFrame").disabled, false, "publication protection does not prevent restoring the frame");

  // The workspace needs its own reachable undo for a real deletion receipt:
  // a toast outside the modal cannot be the only recovery control.
  const savedSingle = JSON.parse(JSON.stringify(state.questions.find((item) => item.id === single.id)));
  const restoreCalls = []; let restoreResult = false, pendingRestore = null, resolveRestore = null;
  host.restoreDeletedBatch = async (paperId, batchId) => {
    restoreCalls.push({ paperId, batchId });
    const result = pendingRestore ? await pendingRestore : restoreResult;
    if (result) state.questions.push(JSON.parse(JSON.stringify(savedSingle)));
    return result;
  };
  deleteResult = { undo_batch: { id: "batch-200" } };
  openRegions(state.questions.find((item) => item.id === single.id));
  await removeMountedPart(0);
  const beforeReceiptDeletionWrites = writes.length;
  await $("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion").emit("click");
  assert.equal(controller.snapshot().layout, ""); assert.equal(dialog.question, null);
  assert.equal(state.questions.some((item) => item.id === single.id), false);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutRestoreDeleted").textContent, "恢复这题");
  assert.match(sidebarText(), /第 40 题已移到回收站/);
  await $("pageLayoutPanel").querySelector("#pageLayoutRestoreDeleted").emit("click");
  assert.deepEqual(restoreCalls, [{ paperId: state.paperId, batchId: "batch-200" }]);
  assert.ok($("pageLayoutPanel").querySelector("#pageLayoutRestoreDeleted"), "a failed or cancelled restore keeps its reachable retry button");
  assert.equal(controller.snapshot().layout, ""); assert.equal(dialog.question, null); assert.equal(dialog.saving, false);
  assert.equal(state.questions.some((item) => item.id === single.id), false);

  pendingRestore = new Promise((resolve) => { resolveRestore = resolve; });
  const restoreButton = $("pageLayoutPanel").querySelector("#pageLayoutRestoreDeleted");
  const restoring = restoreButton.emit("click");
  assert.equal(dialog.saving, true);
  await restoreButton.emit("click"); await controller.begin("add"); await controller.preview();
  assert.equal(restoreCalls.length, 2, "busy restore rejects repeat clicks and other mutations");
  assert.equal(controller.snapshot().layout, "");
  assert.equal(writes.length, beforeReceiptDeletionWrites, "deletion and recovery never submit a new layout target");
  resolveRestore(true); await restoring; pendingRestore = null;
  assert.equal(dialog.saving, false);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutRestoreDeleted"), null, "successful recovery removes the completed retry action");
  assert.equal(state.questions.filter((item) => item.id === single.id).length, 1, "the original question identity is restored exactly once");
  assert.equal(dialog.question.id, single.id); assert.equal(snapshot().sources[0].id, single.id);
  assert.deepEqual(snapshot().targets[0].regions, savedSingle.regions);
  assert.equal(dialog.question.edited_stem, savedSingle.edited_stem);
  assert.equal(controller.dirty(), false);
  assert.ok(surface.querySelector("[data-layout-handle]"), "restored source frames immediately expose their adjustment handles");
  await labelNode().emit("pointerdown", pointer(150, 200));
  await window.emit("pointermove", pointer(154, 200)); await window.emit("pointerup", pointer(154, 200));
  assert.equal(controller.dirty(), true, "the restored saved question is directly adjustable");
  assert.equal(writes.length, beforeReceiptDeletionWrites, "restoring a question does not rebuild it as an added question");

  const beforeRefreshDraft = controller.snapshot().layout, beforeRefreshHistory = JSON.stringify(dialog.history);
  const boundRevision = state.paper.layout_revision;
  state.paper.layout_revision = boundRevision + 10;
  controller.refresh(); await Promise.resolve(); await Promise.resolve();
  assert.equal(controller.snapshot().layout, beforeRefreshDraft, "refresh repaints without replacing an existing unsaved range draft");
  assert.equal(JSON.stringify(dialog.history), beforeRefreshHistory); assert.equal(controller.dirty(), true);
  serverConflict = true; const beforeRefreshSave = writes.length; await controller.preview();
  assert.equal(writes.length, beforeRefreshSave + 1);
  assert.equal(writes.at(-1).body.layout_revision, boundRevision, "refresh never silently rebases the draft's layout revision");
  assert.equal(controller.snapshot().layout, beforeRefreshDraft, "a post-refresh stale save retains the same draft for conflict recovery");
  assert.ok($("pageLayoutPanel").querySelector("#pageLayoutConflict"));
  serverConflict = false; state.paper.layout_revision = boundRevision;

  controller.close(); controller.open("view", null);
  await $("pageLayoutPanel").querySelector("#pageLayoutAdd").emit("click");
  assert.equal(snapshot().kind, "add"); assert.equal(snapshot().targets[0].regions.length, 0);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion"), null, "a new empty draft has no saved question to delete");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutEmptyRange"), null);
  assert.equal($("pageDialogSave").disabled, true);
  openRegions(state.questions[0]); await controller.begin("split");
  await $("pageLayoutPanel").querySelector('[data-layout-target-index="1"]').emit("click");
  assert.equal(snapshot().targets[1].regions.length, 0);
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutDeleteQuestion"), null, "an empty split result cannot delete its saved source question");
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutEmptyRange"), null);
  assert.equal($("pageDialogSave").disabled, true);
  console.log("Original-paper layout: overlap ordering, direct selection, split/merge bounds, pointer thresholds, clean in-place save, manual-only edits, lost-response recovery, return actions, empty-frame recovery and protected question deletion: OK");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
