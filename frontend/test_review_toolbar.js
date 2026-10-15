"use strict";

// What the paper sidebar shows, read against the real markup. Two of these
// guard wording that was once misread from a screenshot and then "fixed" into a
// regression, so they are pinned here on purpose.
const assert = require("node:assert/strict");
const fs = require("node:fs");

const js = fs.readFileSync(require.resolve("./app.js"), "utf8");
const css = fs.readFileSync(require.resolve("./styles.css"), "utf8");

// The paper list carries the full name for hover, so truncation is a reading
// problem rather than a lost name — the fix is wrapping, not removing the title.
assert.match(js, /link\.title = paperDisplayName\(paper\);/,
  "a truncated paper name still reveals itself on hover");
assert.match(js, /el\("span", "paper-file", paperDisplayName\(paper\)\)/,
  "the visible name comes from the same helper the tooltip uses");

const paperFile = css.match(/\.paper-file \{[^}]*\}/)?.[0] || "";
assert.doesNotMatch(paperFile, /text-overflow: ellipsis/,
  "eight papers side by side must not collapse to indistinguishable fragments");
assert.match(paperFile, /-webkit-line-clamp: 2/,
  "the paper name gets two lines before it is clipped");
assert.match(css, /\.paper-meta \{[^}]*text-overflow: ellipsis/,
  "the secondary line stays on one line");

// A row the teacher has already handled reads as done, not as unavailable:
// solid accent fill, white check glyph. Dimming those rows would be a regression.
assert.match(css, /\.chip\.approved \{[^}]*background: var\(--accent\)[^}]*color: #fff/,
  "an approved question carries a solid accent chip with a readable check mark");
assert.match(css, /\.chip\.approved::before \{[^}]*content: "✓"/,
  "the chip says what happened with a glyph, not a colour alone");

// The four toolbar buttons share one style; the green fill is the pressed state
// for 专注 / 放大镜, not a second button design.
assert.match(css, /\.toggle-pill\[aria-pressed="true"\]/,
  "pressed toolbar buttons are distinguished by state, not by a different class");
const pills = (fs.readFileSync(require.resolve("./index.html"), "utf8").match(/class="toggle-pill"/g) || []).length;
assert.ok(pills >= 4, `the toolbar buttons share one class (found ${pills})`);
assert.ok(!/\.toggle-pill\.accent|button-primary[^"]*toggle-pill/.test(css),
  "no toolbar button is promoted to the primary style while others stay plain");

console.log("review toolbar and paper list checks: OK");