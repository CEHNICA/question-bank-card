"use strict";

// The tag directory page opened onto 299 rows all reading "0 题" and never said
// anything about the state that causes it: 807 published questions, two of them
// tagged, and hundreds of AI proposals already waiting to be reviewed. Someone
// walking in could not tell whether the count was broken or the library really
// was untagged, and the page never mentioned the one step that would fix it.
//
// These checks run the real wording function and pin the numbers the server has
// to send. The button's name has to carry the object it acts on, per the same
// rule the rest of the interface follows.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const managerJs = fs.readFileSync(require.resolve("./library-tag-manager.js"), "utf8");
const managerCss = fs.readFileSync(require.resolve("./library-tag-manager.css"), "utf8");
const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const service = fs.readFileSync(require.resolve("../backend/core/library_tagging.py"), "utf8");

// ---- the wording function, executed rather than pattern-matched
const start = managerJs.indexOf("    function usageNote(catalogue) {");
const end = managerJs.indexOf("    function renderUsageNote() {");
assert.ok(start >= 0 && end > start, "usageNote is available for behavior checks");
const noteSource = managerJs.slice(start, end);
assert.ok(noteSource.includes("function usageNote(catalogue) {"),
  "the sliced code really contains usageNote; an empty slice would pass everything below");
const context = vm.createContext({});
vm.runInContext(`${noteSource}\nthis.usageNote = usageNote;`, context);
const usageNote = context.usageNote;

// ---- the state that made the page useless
const sparse = usageNote({ published_questions: 807, tagged_questions: 2, pending_suggestions: 443 });
assert.ok(sparse, "a library with untagged questions still gets an explanation");
assert.match(sparse.text, /^题库 807 道题里只有 2 道贴了标签/, "the note opens with the state, not with advice");
assert.match(sparse.text, /443 条 AI 标签建议等你核对/, "the waiting proposals are counted, not just alluded to");
assert.match(sparse.text, /不用花钱/, "reviewing what AI already proposed does not cost anything");
assert.equal(sparse.button, "回题库核对这 443 条 AI 标签建议",
  "the button names what it acts on, with the count it acts on");
assert.match(sparse.title, /AI 建议待核对/, "the hover says how to get there");

// The counts must follow the numbers, not sit there as fixed text.
assert.match(usageNote({ published_questions: 100, tagged_questions: 1, pending_suggestions: 5 }).text,
  /题库 100 道题里只有 1 道贴了标签/, "a smaller library says its own numbers");
assert.match(usageNote({ published_questions: 100, tagged_questions: 1, pending_suggestions: 5 }).text,
  /另有 5 条 AI 标签建议/, "the proposal count follows the data");
assert.equal(usageNote({ published_questions: 100, tagged_questions: 1, pending_suggestions: 5 }).button,
  "回题库核对这 5 条 AI 标签建议", "the button's number follows the data too");

// ---- nothing to say, so say nothing: no empty bar over the list
for (const catalogue of [
  { published_questions: 0, tagged_questions: 0, pending_suggestions: 0 },
  { published_questions: 807, tagged_questions: 807, pending_suggestions: 0 },
  { published_questions: 807, tagged_questions: 807, pending_suggestions: 443 },
  {},
  null,
  undefined
]) {
  assert.equal(usageNote(catalogue), null,
    "an empty note bar is worse than none: it only shows when there is something to say");
}

// A library that has never been told these counts (an older server) must not
// produce "题库 0 道题里只有 0 道贴了标签".
assert.equal(usageNote({ tags: [], total: 299 }), null,
  "missing counts must not be read as zero questions");

// ---- no proposals left: the advice has to change, not claim there are some
const exhausted = usageNote({ published_questions: 807, tagged_questions: 2, pending_suggestions: 0 });
assert.ok(exhausted, "an untagged library is still worth explaining when nothing is queued");
assert.doesNotMatch(exhausted.text, /0 条 AI 标签建议/, "must not offer to review nothing");
assert.equal(exhausted.button, "回题库给题目贴标签", "with nothing to review, the action changes");
assert.match(exhausted.text, /选中一道题/, "and the sentence explains how to tag by hand");

// ---- the numbers have to actually reach the page
for (const field of ["published_questions", "tagged_questions", "pending_suggestions"]) {
  assert.match(service, new RegExp(`"${field}":`), `the directory response carries ${field}`);
}
assert.match(service, /tagged_questions = len\(\{publication_id for ids in publications_by_tag\.values\(\) for publication_id in ids\}\)/,
  "the tagged count counts questions once, not one row per tag on the same question");
// The same filter was written out twice before; three places to drift apart is worse.
assert.doesNotMatch(service.slice(service.indexOf("def _catalogue_rows()"), service.indexOf("def template_rows()")),
  /publication__status=PublishedQuestion\.Status\.PUBLISHED, needs_review=False\s*\)\s*\n\s*for tag_id/,
  "the published-and-reviewed filter is defined once, not copied per use");

// ---- the button really closes this page and lands on the review filter
assert.match(managerJs, /const goReview = button\("回题库核对 AI 标签建议", \(\) => \{ close\(\); options\.onReviewSuggestions\?\.\(\); \}/,
  "taking the exit closes the directory first, then asks the library to filter");
assert.match(managerJs, /usageNoteBox\.append\(usageNoteText, goReview\)/, "the note holds the text and the exit together");
assert.match(managerJs, /results\.append\(location, listHeading, usageNoteBox, bulk, list, mapHost\)/,
  "the note sits directly above the list, where the 0 题 rows are");
assert.match(managerJs, /const note = usageNote\(state\.catalogue\);\s*\n\s*usageNoteBox\.hidden = !note;/,
  "when there is nothing to say the bar is hidden, not left blank");

// ---- library.js wires the exit to the pending-suggestion filter
assert.match(libraryJs, /onReviewSuggestions: \(\) => \{/,
  "the library provides the handler that filter the directory's exit needs");
assert.match(libraryJs, /state\.tagReview = true;/,
  "arriving from the tag page means landing on the proposals waiting for review");
assert.match(libraryJs, /syncUrl\(\); void load\(\);\s*\n\s*\}/,
  "and the filtered list actually loads");

// ---- it looks like part of the page, not a sticker on top of it
assert.match(managerCss, /\.tag-directory-usage-note \{[^}]*flex-direction: column/,
  "text and button stack, so the sentence gets the full width instead of being squeezed beside the button");
assert.match(managerCss, /\.tag-directory-usage-note\[hidden\] \{ display: none; \}/,
  "hiding it must not leave its layout behind, the same rule the bulk bar follows");

console.log("tag directory state note: OK");
