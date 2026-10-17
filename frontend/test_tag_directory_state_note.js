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
const sparse = usageNote({ published_questions: 807, tagged_questions: 2,
  questions_pending_review: 439, questions_pending_actionable: 435 });
assert.ok(sparse, "a library with untagged questions still gets an explanation");
assert.match(sparse.text, /^题库 807 道题里只有 2 道贴了标签/, "the note opens with the state, not with advice");
assert.match(sparse.text, /439 道题有 AI 标签建议等你核对/, "the waiting questions are counted, not just alluded to");
assert.match(sparse.text, /不用花钱/, "reviewing what AI already proposed does not cost anything");
assert.equal(sparse.button, "回题库核对这 439 道题的 AI 标签建议",
  "the button names what it acts on, with the count it acts on");
assert.match(sparse.title, /AI 建议待核对/, "the hover says how to get there");
// 4 of the 439 cannot be accepted as they stand, so the hover must split them out.
// Both sides of that check are pinned: an earlier version accepted either wording,
// which let "everything is acceptable right now" slip through even though 4 questions
// are not.
assert.match(sparse.title, /其中 435 道现在就能采纳，另外 4 道对不上现在的标签库/,
  "the hover separates what can be accepted now from what has to be regenerated");
assert.doesNotMatch(sparse.title, /439 道题现在都能采纳/,
  "claiming every one of them is acceptable when 4 are not is the thing this guards against");
// The all-clear branch still exists, for a library with nothing outstanding.
const allClear = usageNote({ published_questions: 807, tagged_questions: 2,
  questions_pending_review: 439, questions_pending_actionable: 439 });
assert.match(allClear.title, /这 439 道题现在都能采纳/,
  "when nothing is outstanding the hover says so plainly, without inventing a split");

// The counts must follow the numbers, not sit there as fixed text.
assert.match(usageNote({ published_questions: 100, tagged_questions: 1,
  questions_pending_review: 5, questions_pending_actionable: 5 }).text,
  /题库 100 道题里只有 1 道贴了标签/, "a smaller library says its own numbers");
assert.match(usageNote({ published_questions: 100, tagged_questions: 1,
  questions_pending_review: 5, questions_pending_actionable: 5 }).text,
  /另有 5 道题有 AI 标签建议/, "the pending count follows the data");
assert.equal(usageNote({ published_questions: 100, tagged_questions: 1,
  questions_pending_review: 5, questions_pending_actionable: 5 }).button,
  "回题库核对这 5 道题的 AI 标签建议", "the button's number follows the data too");

// Nothing to say, so say nothing: no empty bar over the list.
for (const catalogue of [
  { published_questions: 0, tagged_questions: 0, questions_pending_review: 0, questions_pending_actionable: 0 },
  { published_questions: 807, tagged_questions: 807, questions_pending_review: 0, questions_pending_actionable: 0 },
  { published_questions: 807, tagged_questions: 807, questions_pending_review: 439, questions_pending_actionable: 435 },
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
const exhausted = usageNote({ published_questions: 807, tagged_questions: 2,
  questions_pending_review: 0, questions_pending_actionable: 0 });
assert.ok(exhausted, "an untagged library is still worth explaining when nothing is queued");
assert.doesNotMatch(exhausted.text, /0 道题有 AI 标签建议/, "must not offer to review nothing");
assert.equal(exhausted.button, "回题库给题目贴标签", "with nothing to review, the action changes");
assert.match(exhausted.text, /选中一道题/, "and the sentence explains how to tag by hand");

// ---- the numbers have to actually reach the page
for (const field of ["published_questions", "tagged_questions", "questions_pending_review", "questions_pending_actionable"]) {
  assert.match(service, new RegExp(`"${field}":`), `the directory response carries ${field}`);
}
assert.match(service, /tagged_questions = len\(\{publication_id for ids in publications_by_tag\.values\(\) for publication_id in ids\}\)/,
  "the tagged count counts questions once, not one row per tag on the same question");

// 1.25.13：待核对这个数必须和题库页那个筛选是同一个，不能各数一遍。
// 上一版这里数建议条数（443）、题库页数题目数（439）—— 一道题可以挂多条建议。
const catalogueFn = service.slice(service.indexOf("def _catalogue_rows()"), service.indexOf("def template_rows()"));
assert.match(catalogueFn, /from \.library_browse import tag_work_counts/,
  "the pending counts come from the same function the library list uses");
assert.match(catalogueFn, /"questions_pending_review": tag_work\["pending_review"\]/,
  "and the review count is that function's number, not a second opinion");
assert.match(catalogueFn, /"questions_pending_actionable": tag_work\["pending_actionable"\]/,
  "the actionable count comes along too, so the hover can separate the two");
assert.doesNotMatch(catalogueFn, /LibraryTagSuggestion\.objects\.filter\(\s*status=LibraryTagSuggestion\.Status\.PENDING\)\.count\(\)/,
  "counting proposal rows here is what produced 443 next to the library's 439");

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

// ---- 1.25.13：空篮子以前只叫人来把题放进来，却没说在哪儿放
// 断的是源码文本，所以下面那句旧文案在注释里也不能原样复述 —— 否则这条断言永远红，
// 于是有人会去改断言，而不是把文案改对。注释只说它曾经缺了什么。
assert.match(libraryJs, /!state\.basket\.length \? "篮子是空的。在左边任意一道题的右下角点「加入试题篮」/,
  "the empty basket points at the control a person has to press");
assert.doesNotMatch(libraryJs, /题加入这里/,
  "the old line asked for the questions without saying where the button is");
// It has to be the same words the button actually wears, or the instruction is useless.
const addButton = libraryJs.match(/"(加入试题篮)"/);
assert.ok(addButton, "the add-to-basket button still exists");
assert.match(libraryJs, new RegExp(`「${addButton[1]}」`),
  "the instruction quotes the button's own label rather than a paraphrase");

// ---- it looks like part of the page, not a sticker on top of it
assert.match(managerCss, /\.tag-directory-usage-note \{[^}]*flex-direction: column/,
  "text and button stack, so the sentence gets the full width instead of being squeezed beside the button");
assert.match(managerCss, /\.tag-directory-usage-note\[hidden\] \{ display: none; \}/,
  "hiding it must not leave its layout behind, the same rule the bulk bar follows");

console.log("tag directory state note: OK");
