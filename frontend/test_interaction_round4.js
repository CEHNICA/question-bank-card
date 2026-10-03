"use strict";

// Round 4: review, upload, figure and library interaction.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const App = require("./app.js");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const libraryCss = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");

// A background paper that finishes is announced once; the open one is not.
const previous = new Map([["a", "reading"], ["b", "reading"], ["c", "ready"], ["d", "queued"]]);
const papers = [
  { id: "a", status: "ready", counts: { total: 23, yellow: 1 } },
  { id: "b", status: "ready", counts: { total: 20 } },
  { id: "c", status: "ready", counts: { total: 19 } },
  { id: "d", status: "parsing", counts: {} },
];
assert.deepEqual(App.justFinished(previous, papers, "b").map((paper) => paper.id), ["a"]);
assert.equal(App.finishedMessage(papers[0], "gaokao"), "“gaokao”已读完：23 题，1 张要看");
assert.equal(App.finishedMessage(papers[1], "lingxing"), "“lingxing”已读完：20 题，全部识读完成");
assert.equal(App.finishedMessage({ status: "failed" }, "x"), "“x”处理失败");
assert.equal(App.title("题有据", 2), "（2 份读完）题有据");
assert.equal(App.title("题有据", 0), "题有据");
assert.match(js, /item\.classList\.add\("fresh"\)/);

// The review meter is one row; its explanation opens on demand.
assert.match(html, /<details class="meter-note">/);
assert.match(css, /\.review-meter \{[^}]*display: flex/);
assert.match(js, /先处理 \$\{c\.todo\} 张重点核查的卡/);

// Long cards keep their buttons on screen; while editing, the original stays
// in the sticky left column (1.10.1: every card is crop left, text right).
assert.match(css, /\.card:not\(\.compact\) \.card-actions \{[^}]*position: sticky; bottom: 0/);
assert.match(css, /\.source-sticky \{ position: sticky;/);
assert.doesNotMatch(js, /wide-source/);
assert.match(js, /card\.classList\.remove\("editing"\)/);
assert.match(css, /\.toast \{ position: fixed; right: 24px; bottom: 78px;/);

// After a mouse click on a filter tab the review keys work straight away.
assert.match(js, /if \(event\.detail > 0\) \{\s*const first = state\.questions\.find\(visible\)/);

// Files can be dropped anywhere in the window.
assert.match(html, /id="dropOverlay"/);
assert.match(js, /window\.addEventListener\("drop", \(event\) => \{[\s\S]*handleFiles\(event\.dataTransfer\.files\)/);
assert.match(js, /const carriesFiles = \(event\) =>/);
assert.match(html, /class="material-options"/);
assert.match(css, /\.material-type:has\(input\[value="book"\]:checked\) \.material-hint \{ display: block; \}/);

// Figure owners by key; the viewer offers editing.
assert.match(js, /const SLOT_KEYS = \{ s: "stem", a: "A", b: "B", c: "C", d: "D", e: "E", x: "irrelevant", j: "join" \}/);
assert.match(html, /data-figure-slot="irrelevant" aria-keyshortcuts="X"/);
assert.match(html, /id="viewerEdit"/);
assert.match(js, /key\.toLowerCase\(\) === "r" \|\| key\.toLowerCase\(\) === "f"/);

// Library: withdrawing is quiet in the list; the basket is arranged in the preview.
assert.match(css, /\.library-withdraw \{ color: var\(--muted\) !important; \}/);
assert.match(libraryJs, /function printTools\(items, group, position, number\)/);
assert.match(libraryCss, /\.print-question-tools/);
assert.match(libraryCss, /\.library-main \{ max-width: 1480px;/);
assert.match(libraryCss, /@media \(max-width: 979px\)/);

// Finishing a paper offers the next one that still has cards to review.
const list = [
  { id: "1", status: "ready", counts: { total: 10, approved: 10 } },
  { id: "2", status: "ready", counts: { total: 10, approved: 10 } },
  { id: "3", status: "reading", counts: { total: 10 } },
  { id: "4", status: "ready", counts: { total: 12, approved: 3, yellow: 2 } },
];
assert.equal(App.nextToReview(list, "2").id, "4");
assert.equal(App.nextToReview(list, "4"), null);
assert.match(js, /下一份：\$\{paperDisplayName\(next\)\}/);


// A textbook example is headed “例 N”: its printed label is no longer in the stem.
assert.match(js, /q\?\.source_kind === "example" \? `例 \$\{q\.number\}` : `第 \$\{q\.number\} 题`/);
assert.match(js, /el\("span", "qnum", questionLabel\(q\)\)/);

console.log("round 4 interaction checks: OK");
