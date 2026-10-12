"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

// A dropped question used to vanish without a trace: 「移出试题篮」cleared missing,
// which hid the only notice, cleared the paper's red incomplete banner and unlocked
// every export button. The teacher then printed a paper that was quietly short of
// questions they had picked. Keep the notice alive after the last missing row is gone.
class El {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.className = ""; this.textContent = "";
    this.hidden = false; this.disabled = false; this.listeners = new Map(); this.attributes = {};
  }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() { focused.current = this; }
  click() { this.listeners.get("click")?.(); }
  get text() { return [this.textContent, ...this.children.map(k => k.text)].join("\n"); }
  findButton(label) { return this.children.flatMap(k => [k, ...(k.children || [])]).find(k => k.textContent === label); }
}

const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new El()); return elements.get(id); };
const focused = { current: null };

const state = { basket: ["a", "b", "c"], catalog: new Map(), features: { ai_answer: false } };
const printState = { token: 0, items: [{ id: "a" }, { id: "b" }], missing: [], dropped: [], loading: false,
  exporting: false, availableAnswers: 0, tooWide: 0 };
let rendered = 0;
let saved = 0;
let refreshed = 0;

const node = (tag, className = "", text) => { const el = new El(tag); el.className = className; el.textContent = text ?? ""; return el; };
const sandbox = {
  "$": get, node, state, printState, console,
  document: { activeElement: null, createElement: tag => new El(tag) },
  setTimeout, clearTimeout,
  renderPrint() { rendered++; sandbox.renderPrintMissing(); },
  render() { rendered++; },
  saveBasket() { saved++; },
  openPrint() { refreshed++; },
  swapBasketForReplacement() { return false; },
  missingReason(item) { return item.reason === "withdrawn" ? "这道题已撤回" : "这道题已不存在"; },
};
vm.createContext(sandbox);
vm.runInContext(
  source.slice(source.indexOf("  function syncDroppedQuestions()"),
    source.indexOf("  // 换序、移出之后，焦点要跟着题走。")),
  sandbox,
);

const box = () => get("printMissing");
const shown = () => !box().hidden;
const flat = el => [el, ...el.children.flatMap(flat)];

function reset(missing) {
  printState.missing = missing;
  printState.dropped = [];
  printState.items = [{ id: "a" }, { id: "b" }];
  state.basket = ["a", "b", "c"];
  sandbox.renderPrintMissing();
}
const button = label => flat(box()).find(k => k.tag === "button" && k.textContent === label);

// Nothing wrong: no notice at all.
reset([]);
assert.equal(shown(), false, "A complete paper needs no notice");

// Two questions have not loaded: the notice names them and export stays blocked.
reset([{ id: "c", label: "第三题", reason: "not_found" }]);
assert.equal(shown(), true, "Unloaded questions must raise a notice");
assert.match(box().text, /尚未载入/, "The notice explains what is wrong");
assert.ok(button("重试未载入题目"), "Retry stays available while a row is pending");

// The teacher gives up on that one and drops it. This used to end every trace of it.
button("移出试题篮").click();
assert.equal(printState.missing.length, 0, "Dropping the row clears the pending list");
assert.equal(printState.dropped.length, 1, "Dropping is remembered even after the row is gone");
assert.deepEqual(printState.dropped.map(row => row.id), ["c"]);
assert.equal(state.basket.includes("c"), false, "The question leaves the basket for good");
assert.ok(saved >= 1, "The shortened basket is persisted");

// The whole point: the notice does not disappear with the last missing row.
assert.equal(shown(), true, "A paper that lost a question must keep telling the teacher");
const text = box().text;
assert.match(text, /1/, "The notice says how many questions this paper is short by");
assert.match(text, /移出/, "The notice says those questions were dropped, not merely missing");
assert.ok(!/尚未载入/.test(text), "Once nothing is pending the wording must switch to the dropped case");
assert.equal(button("移出试题篮"), undefined, "A dropped question cannot be dropped twice");
assert.equal(button("重试未载入题目"), undefined, "Retry is pointless once nothing is pending");

// Two dropped, one still pending: both facts in one notice.
reset([{ id: "c", label: "第三题", reason: "not_found" }]);
printState.dropped = [{ id: "b", label: "第二题" }];
sandbox.renderPrintMissing();
const mixed = box().text;
assert.match(mixed, /尚未载入/, "Pending rows are still reported");
assert.match(mixed, /已移出 1 题/, "Dropped rows are reported next to them, not instead of them");
assert.ok(button("移出试题篮"), "A pending row can still be dropped");
assert.ok(button("重试未载入题目"), "Retry is still offered");

// Dropping twice must not double-count.
reset([{ id: "c", label: "第三题", reason: "not_found" }, { id: "d", label: "第四题", reason: "withdrawn" }]);
state.basket = ["a", "b", "c", "d"];
sandbox.renderPrintMissing();
button("移出试题篮").click();
button("移出试题篮").click();
assert.equal(printState.dropped.length, 2, "Each question is counted once");
assert.deepEqual(printState.dropped.map(row => row.id).sort(), ["c", "d"]);

// Putting a dropped question back restores the notice's right to stay hidden.
state.basket = ["a", "b", "c", "d"];
sandbox.syncDroppedQuestions();
sandbox.renderPrintMissing();
assert.equal(printState.dropped.length, 0, "A question back in the basket is no longer missing from the paper");
assert.equal(shown(), false, "Re-adding the question makes the paper complete again");

// Reloading a paper is the normal thing that happens on every export; it must not
// quietly forget what the teacher dropped.
printState.dropped = [{ id: "c", label: "第三题" }, { id: "e", label: "第五题" }];
state.basket = ["a", "b", "c"];
sandbox.syncDroppedQuestions();
assert.deepEqual(printState.dropped.map(row => row.id), ["e"],
  "A reloaded paper keeps only what is still outside the basket; the question put back is no longer lost");

// Reloading a paper is the only thing that prunes the record, and it happens on
// every single export. Without the call the list would grow forever and a question
// the teacher had deliberately put back would still be reported as lost.
const reload = source.slice(source.indexOf("  async function openPrint("), source.indexOf("  function markDraftDirty("));
// Matched with an anchored pattern on purpose: a commented-out call still contains
// the substring, and this assertion must not pass on a comment.
assert.ok(/^[ \t]*syncDroppedQuestions\(\);/m.test(reload),
  "Every reload has to prune questions that went back into the basket");
assert.ok(/printState\.missing = missing;[\s\S]{0,400}^[ \t]*syncDroppedQuestions\(\);/m.test(reload),
  "The prune must run after the pending list is refreshed, not before");
assert.ok(/^[ \t]*printState\.dropped = printState\.dropped\.filter\(\(row\) => row\.id !== item\.id\)\.concat\(item\);/m.test(source),
  "Dropping a question is what records it in the first place");

console.log("print dropped-question notice: OK");