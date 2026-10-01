"use strict";

// Focus: the card being read shows normally, the others dim; the card being
// read follows the scroll.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { readingLine, nearestToLine } = require("./app.js");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

assert.match(html, /id="focusToggle"[^>]*aria-pressed="true"[^>]*>.*专注<\/button>/);
assert.match(html, /专注开 \/ 关（其余题暗下来）<\/span><span><kbd>Z<\/kbd>/);
// On by default, remembered per viewer.
assert.match(js, /focus: readPref\("qb-focus", "1"\) === "1"/);
assert.match(js, /case "z": event\.preventDefault\(\); setFocus\(!state\.focus\)/);
// Only the current card is lit; selection mode (looking across cards) is not dimmed.
assert.match(css, /\.cards\.focus-mode\.reading:not\(\.selecting\) \.card:not\(\.is-current\) \{ opacity: \.34;/);
assert.match(css, /\.cards\.focus-mode \{ gap: 26px; \}/);
assert.match(css, /\.cards\.focus-mode \.card\.compact \+ \.card\.compact \{ margin-top: -14px; \}/);
// Dimming needs a full card being read: a collapsed (approved) row, a card
// scrolled away or a screen of collapsed rows dims nothing.
assert.match(js, /const reading = Boolean\(state\.focus && card && !card\.classList\.contains\("compact"\) && \(state\.followHold \|\| onScreen\(card\)\)\);/);
assert.match(js, /container\.classList\.toggle\("reading", reading\);/);
// Collapsed rows are never picked as the card being read.
assert.match(js, /const cards = cardNodes\(\)\.filter\(\(card\) => !card\.classList\.contains\("compact"\) && onScreen\(card\)\);/);
// In full screen the top bar is 0 px high, which is not "missing".
assert.match(js, /return \(bar \? bar\.offsetHeight : 56\) \+/);
// The card being read follows the scroll, but not while a jump scrolls past cards.
assert.match(js, /window\.addEventListener\("scroll", \(\) => \{\s*if \(!state\.focus \|\| followFrame\) return;/);
assert.match(js, /if \(card && scroll\) state\.followHold = true;/);
assert.match(js, /if \(!state\.followHold\) \{\s*const card = readingCard\(\);/);
assert.match(js, /window\.addEventListener\("wheel", releaseFollow, \{ passive: true \}\)/);
// The card being read sits under a reading line a third of the way down;
// the line starts at the top of the page and runs to the bottom at its end.
const band = { top: 100, bottom: 700 };
assert.equal(readingLine({ ...band, scrollY: 2000, below: 2000 }), 300);
assert.equal(readingLine({ ...band, scrollY: 0, below: 2000 }), 100);
assert.equal(readingLine({ ...band, scrollY: 2000, below: 0 }), 699);
assert.equal(readingLine({ ...band, scrollY: 2000, below: 200 }), 500);
// Under the line wins, even when a taller neighbour shows more of itself.
const rects = [{ top: -400, bottom: 280 }, { top: 300, bottom: 520 }, { top: 546, bottom: 1600 }];
assert.equal(nearestToLine(rects, 300), 1);
assert.equal(nearestToLine(rects, 699), 2);
// A line in the gap between cards takes the nearer one.
assert.equal(nearestToLine(rects, 290), 0);
assert.equal(nearestToLine(rects, 535), 2);
assert.equal(nearestToLine([], 300), -1);

console.log("focus mode checks: OK");

// Full-screen review: the top bar, paper list and paper header fold away; the
// toolbar keeps the paper name and progress.  Esc or Q leaves it.
assert.match(html, /id="fullscreenToggle"[^>]*aria-pressed="false"/);
assert.match(css, /:root\.review-fullscreen \{ --topbar-h: 0px; \}/);
assert.match(css, /:root\.review-fullscreen \.topbar, :root\.review-fullscreen \.sidebar, :root\.review-fullscreen \.paper-head,/);
assert.match(js, /root\.requestFullscreen\(\{ navigationUI: "hide" \}\)\.catch\(\(\) => \{\}\)/);
assert.match(js, /document\.addEventListener\("fullscreenchange", \(\) => \{\s*if \(!document\.fullscreenElement\) setReviewFullscreen\(false\);/);
assert.match(js, /case "q":\s*event\.preventDefault\(\);\s*setReviewFullscreen/);
assert.match(js, /\$\("toolbarPaper"\)\.replaceChildren\(el\("strong", "", paperDisplayName/);
console.log("full-screen review checks: OK");
