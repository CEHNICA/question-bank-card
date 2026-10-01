"use strict";

// Focus: the card being read shows normally, the others dim; the card being
// read follows the scroll.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

assert.match(html, /id="focusToggle"[^>]*aria-pressed="true"[^>]*>.*专注<\/button>/);
assert.match(html, /专注开 \/ 关（其余题暗下来）<\/span><span><kbd>Z<\/kbd>/);
// On by default, remembered per viewer.
assert.match(js, /focus: readPref\("qb-focus", "1"\) === "1"/);
assert.match(js, /case "z": event\.preventDefault\(\); setFocus\(!state\.focus\)/);
// Only the current card is lit; selection mode (looking across cards) is not dimmed.
assert.match(css, /\.cards\.focus-mode\.has-current:not\(\.selecting\) \.card:not\(\.is-current\) \{ opacity: \.34;/);
assert.match(css, /\.cards\.focus-mode \{ gap: 26px; \}/);
// The card being read follows the scroll, but not while a jump scrolls past cards.
assert.match(js, /window\.addEventListener\("scroll", \(\) => \{\s*if \(!state\.focus \|\| followFrame\) return;/);
assert.match(js, /if \(card && scroll\) state\.followHold = true;/);
assert.match(js, /if \(state\.followHold\) return;/);
assert.match(js, /window\.addEventListener\("wheel", releaseFollow, \{ passive: true \}\)/);
// The card being read is the one with the most of it on screen.
assert.match(js, /const shown = Math\.min\(rect\.bottom, window\.innerHeight\) - Math\.max\(rect\.top, top\);/);

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
