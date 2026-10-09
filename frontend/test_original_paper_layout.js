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
  prepend(...nodes) { const old = [...this.children]; this.replaceChildren(...nodes, ...old); }
  focus() {} scrollIntoView() {} setPointerCapture() {} releasePointerCapture() {}
}
async function run() {
  global.window = new Node(); global.crypto ||= require("node:crypto").webcrypto;
  const nodes = new Map(); const $ = (id) => { if (!nodes.has(id)) { const node = new Node(); node.id = id; nodes.set(id, node); } return nodes.get(id); };
  const dialog = { mode: "view", page: 0, imageReady: true, saving: false, tool: "select", spacePan: false };
  const state = { paperId: 7, paper: { id: 7, layout_revision: 9, pages, question_groups: [{ id: 10, title: "组10" }, { id: 20, title: "组20" }] }, questions: JSON.parse(JSON.stringify(questions)) };
  const writes = [], messages = []; let controller, serverConflict = false, serverLoseResponse = false, lookupError = false, receipt = null, surface;
  let historyData = { operations: [], latest_operation: null };
  const host = { dialog, state, $, el: (tag, cls, text) => new Node(tag, cls, text),
    button: (text, cls, fn) => { const n = new Node("button", cls, text); n.addEventListener("click", fn); return n; },
    api: async (path, options = {}) => {
      if (options.method === "POST") {
        writes.push({ path, body: options.body });
        if (serverConflict) { const e = new Error("版本冲突"); e.status = 409; throw e; }
        const request = options.body;
        const updated = state.questions.map((item) => item.id === request.sources[0]?.id ? { ...item, regions: request.targets[0].regions, content_revision: item.content_revision + 1 } : item);
        const result = { paper: { ...state.paper, layout_revision: state.paper.layout_revision + 1 }, questions: updated, operation: { id: 50, target_ids: [1], kind: request.kind } };
        if (serverLoseResponse) { receipt = result; throw new TypeError("connection lost after commit"); }
        return result;
      }
      if (path.includes("client_request_id=")) { if (lookupError) throw new TypeError("lookup disconnected"); return receipt || { operation: null }; }
      return { ...historyData, operation: null, layout_revision: state.paper.layout_revision };
    },
    toast: (message) => messages.push(message), cropView: () => new Node(), renderStage: () => { surface = new Node("div", "stage-surface"); controller?.renderSurface(surface); }, renderPageTabs() {},
    goToDialogPage: (page) => { dialog.page = page; }, pointFrom: (e) => [e.clientX, e.clientY], placeBox: (node, bbox) => { node.bbox = [...bbox]; },
    setCropSaving: (saving) => { dialog.saving = saving; }, trackCropDraft() {}, confirmDialog: async () => true,
    TYPE_NAMES: { unknown: "未定", free_response: "解答题" }, questionCompare: (x, y) => x.id - y.id, rereadQuestion: () => { throw Error("Layout must never call recognition"); },
    isApproved: (item) => Boolean(item.approved), applyCanonical: (data) => { state.paper = data.paper; state.questions = data.questions; }, reloadCanonical: async () => {}
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
  controller.preview(); assert.equal(writes.length, 0, "preview never writes");
  await $("pageLayoutPanel").querySelector("#pageLayoutConfirmSave").emit("click");
  assert.equal(writes.length, 1); assert.equal(writes[0].path, "/api/papers/7/question-layout");
  assert.deepEqual(Object.keys(writes[0].body.targets[0]), ["regions"]);
  assert.equal(dialog.page, 0); assert.equal(dialog.mode, "view", "save stays in the original-paper workspace");
  assert.equal(state.questions[0].edited_stem, "人工文字", "manual text survives canonical save");
  assert.equal(controller.snapshot().layout, "");
  await controller.begin("split"); assert.equal(snapshot().targets.length, 2);
  await $("pageLayoutPanel").querySelector('[data-layout-target-index="1"]').emit("click");
  assert.equal(dialog.boxes.length, 0, "target selection keeps distinct range lists");
  dialog.page = 1; await $("pageLayoutPanel").querySelector("#pageLayoutDraw").emit("click"); host.renderStage();
  await surface.emit("pointerdown", pointer(100, 300)); await surface.emit("pointerdown", pointer(500, 700));
  assert.equal(snapshot().targets[1].regions[0].page_idx, 1); controller.restore(false); assert.equal(snapshot().targets[1].regions.length, 0); controller.restore(true);
  // Start a fresh region draft and verify 409 retains draft/revision for review.
  controller.close(); dialog.page = 0; controller.open("regions", state.questions[0]); host.renderStage();
  await labelNode().emit("pointerdown", pointer(14, 10)); await window.emit("pointermove", pointer(18, 10)); await window.emit("pointerup", pointer(18, 10));
  const revision = state.paper.layout_revision; state.paper.layout_revision = 100; serverConflict = true; controller.preview();
  await $("pageLayoutPanel").querySelector("#pageLayoutConfirmSave").emit("click");
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
  serverConflict = false; serverLoseResponse = true; lookupError = true; controller.preview(); const beforeWrites = writes.length;
  await $("pageLayoutPanel").querySelector("#pageLayoutConfirmSave").emit("click");
  assert.equal(writes.length, beforeWrites + 1); assert.equal(controller.hasUnknownSave(), true);
  await controller.begin("add"); assert.equal(snapshot().kind, "regions", "unknown save blocks switching and duplicate writes");
  lookupError = false; await $("pageLayoutPanel").querySelector("#pageLayoutRetryLookup").emit("click");
  assert.equal(writes.length, beforeWrites + 1, "receipt recovery never repeats the layout POST");
  assert.equal(controller.hasUnknownSave(), false); assert.equal(controller.snapshot().layout, "");
  const valid = { id: 50, kind: "regions", can_undo: true, created_at: "2026-10-09T00:00:00Z" };
  historyData = { operations: [{ id: 51, kind: "regions", can_undo: false, created_at: "2026-10-09T00:01:00Z", blocked_reason: "NO_CHANGE" }, valid], latest_operation: valid };
  controller.close(); controller.open("view", state.questions[0]); await Promise.resolve(); await Promise.resolve();
  assert.equal($("pageLayoutPanel").querySelector("#pageLayoutUndoSaved").disabled, false, "no-op receipt never replaces the latest undoable saved operation");
  dialog.page = 1;
  await $("pageLayoutPanel").querySelector('[data-layout-question-id="3"]').emit("click");
  assert.equal(dialog.page, 0, "question-list selection locates the question's first page");
  await $("pageLayoutPanel").querySelector('[data-layout-question-id="1"]').emit("click");
  await $("pageLayoutPanel").querySelector("#pageLayoutAdd").emit("click");
  assert.equal(snapshot().kind, "add");
  assert.deepEqual(snapshot().sources, [], "clicking supplement after selecting a question submits no source rows");
  assert.equal(snapshot().targets[0].group_id, 10, "supplement starts in the previously selected question group");
  controller.close(); state.paper.archived = true;
  controller.open("regions", state.questions[0]);
  assert.equal(controller.snapshot().layout, "", "archived originals open for inspection without an editable draft");
  for (const id of ["pageLayoutAdd", "pageLayoutEdit", "pageLayoutSplit", "pageLayoutMerge", "pageLayoutUndoSaved", "pageLayoutReread"]) {
    assert.equal($("pageLayoutPanel").querySelector(`#${id}`).disabled, true, `${id} remains read-only while archived`);
  }
  await controller.begin("add");
  assert.equal(controller.snapshot().layout, "", "programmatic action cannot bypass archived read-only state");
  console.log("Original-paper layout: overlap ordering, six persistent colors, split/merge bounds, manual-only payloads, pointer thresholds/cancellation, cross-page undo/redo, in-place save and conflict preservation: OK");
}
run().catch((error) => { console.error(error); process.exitCode = 1; });
