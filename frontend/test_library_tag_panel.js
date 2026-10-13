"use strict";

// A question's tags must be editable in exactly one place. The panel used to offer a
// second set of accept/reject buttons next to the picker, so the same decision could be
// made twice and the two surfaces could disagree. These checks pin the merged shape:
// one dialog holding what the question already has, what AI proposed, and the directory.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");

// ---- the dialog owns all three blocks
assert.match(libraryJs, /const now = node\("div", "tag-picker-now"\); now\.id = "tagPickerNow";/,
  "the tag dialog reserves one container for the question's own tags");
assert.match(libraryJs, /tagDialog\.append\(header, now, more, search, quick, body, footer\)/,
  "that container sits above the directory list, so it is the first thing read");
assert.match(libraryJs, /moreTitle\.append\(node\("b", "", "想加的"\)/,
  "the directory below is labelled too, so all three blocks read the same way");
assert.match(libraryJs, /more\.append\(moreTitle, tools\)/,
  "creating a new label belongs with the block it adds to");
assert.match(libraryJs, /"已经贴上的、AI 建议的、想加的，都在这个窗口里。"/,
  "the dialog says up front that one window holds all three");

const start = libraryJs.indexOf("  function renderTagPickerNow() {");
const end = libraryJs.indexOf("  function renderTagPicker() {", start);
assert.ok(start >= 0 && end > start, "the single-panel renderer is available for behavior checks");
const painter = libraryJs.slice(start, end);
assert.match(painter, /已经在这道题上/, "the first block says what the question already carries");
assert.match(painter, /AI 建议的/, "the second block says what AI proposed");
assert.match(painter, /if \(tagTargetIds\.length !== 1\) \{ box\.hidden = true; return; \}/,
  "a batch tidy has no single question to describe, so the blocks step aside");
assert.match(libraryJs.slice(libraryJs.indexOf("  function renderTagPicker() {"), libraryJs.indexOf("  async function toggleTag(")),
  /renderTagPickerNow\(\);/,
  "every repaint of the dialog redraws the question's own tags, so a removal shows up at once");

// ---- the panel points at the dialog instead of offering a second set of buttons
const extrasStart = libraryJs.indexOf("  function extrasNode(item,");
const extrasEnd = libraryJs.indexOf("    Object.entries(item.job_errors || {})", extrasStart);
assert.ok(extrasStart >= 0 && extrasEnd > extrasStart, "the question panel region is available for checks");
const extras = libraryJs.slice(extrasStart, extrasEnd);
assert.match(extras, /item\.tag_suggestion\?\.status === "pending"/,
  "only a proposal that still needs a decision is redirected into the dialog");
assert.match(extras, /} else \{\s*const panel = tagSuggestionNode\(item\); if \(panel\) box\.append\(panel\);/,
  "proposals that are not awaiting a decision keep their plain status line");
assert.match(extras, /open\.addEventListener\("click", \(\) => openTagEditor\(item\)\)/,
  "the panel entry opens the one place where tags are decided");
assert.equal((extras.match(/tagSuggestionNode\(item\)/g) || []).length, 1,
  "the panel must not render a second copy of the proposal card");
assert.match(extras, /AI 有 \$\{pendingCount\} 条建议待核对/,
  "the panel still says how many are waiting, so nothing disappears silently");

// ---- behavior: the blocks render from real state
class Element {
  constructor(tag, cls = "", text = "") {
    this.tagName = tag; this.className = cls; this.textContent = text;
    this.children = []; this.hidden = false; this.dataset = {};
    this.style = { setProperty() {} }; this.listeners = {};
  }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute() {}
}
function textOf(element) {
  if (typeof element === "string") return element;
  return [element.textContent || "", ...element.children.map(textOf)].join(" ").replace(/\s+/g, " ").trim();
}
function walk(element, matches, out = []) {
  if (!element || typeof element === "string") return out;
  if (matches(element)) out.push(element);
  element.children.forEach(child => walk(child, matches, out));
  return out;
}

const box = new Element("div", "tag-picker-now");
const suggestionCard = new Element("aside", "tag-suggestion-card");
const removed = [];
const context = vm.createContext({
  node: (tag, cls = "", text = "") => new Element(tag, cls, text),
  $: id => (id === "tagPickerNow" ? box : null),
  state: { catalog: new Map([["pub-1", {
    id: "pub-1",
    tag_suggestion: { id: "sug-1", status: "pending", message: "提示", suggestions: [{}, {}, {}] }
  }]]) },
  tagDialog: { classList: { contains: () => false } },
  tagTargetIds: ["pub-1"],
  tagMemberships: { "pub-1": new Set(["tag-1"]) },
  tagCatalogue: { tags: [{ id: "tag-1", name: "平行四边形", color: "#123456" }] },
  tagSuggestionNode: () => suggestionCard,
  toggleTag: (tag, remove) => { removed.push([tag.name, remove]); },
});
vm.runInContext(painter, context);
context.renderTagPickerNow();

assert.equal(box.hidden, false, "a single question shows its own block");
assert.match(textOf(box), /已经在这道题上/);
assert.match(textOf(box), /平行四边形/, "the tag already on the question is listed, not just a count");
assert.match(textOf(box), /AI 建议的/, "AI's proposals share the same window");
assert.match(textOf(box), /3 条待核对/, "the proposal count is shown next to the block");
assert.ok(walk(box, node => node === suggestionCard).length, "the real proposal card is reused, not reimplemented");

const drop = walk(box, node => node.className === "tag-picker-current-drop")[0];
assert.ok(drop, "each owned tag offers a way to take it off");
assert.match(drop.title, /从这道题移除「平行四边形」/, "the removal control names the tag it removes");
drop.listeners.click[0]();
assert.deepEqual(removed, [["平行四边形", true]], "removing goes through the same save path as the list");

box.replaceChildren();
context.tagTargetIds = ["pub-1", "pub-2"];
context.renderTagPickerNow();
assert.equal(box.hidden, true, "a batch tidy drops the single-question blocks instead of describing one of many");
assert.equal(box.children.length, 0);

// ---- the block has to scroll, or the status bar is pushed off the dialog
assert.match(libraryCss, /\.tag-picker-now \{[^}]*max-height: [^;]+;/,
  "the question's own blocks are capped so they cannot push the status bar away");
assert.match(libraryCss, /\.tag-picker-now \{[^}]*overflow-y: auto;/,
  "long proposal lists scroll inside the dialog");
assert.match(libraryCss, /\.tag-picker-now\[hidden\] \{ display: none; \}/,
  "a hidden block takes no space in batch mode");

console.log("single tag panel checks: OK");