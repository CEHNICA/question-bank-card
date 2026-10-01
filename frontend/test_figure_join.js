"use strict";

// A table cut by a page break: the lower piece joins the figure before it.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { joinHost, figuresFromBoxes } = require("./app.js");

const top = { page_idx: 2, bbox: [100, 820, 900, 990], slot: "stem", candidate_key: "2:100,820,900,990" };
const bottom = { page_idx: 3, bbox: [100, 20, 900, 70], slot: "stem", join: true, candidate_key: "3:100,20,900,70" };
const k2 = { page_idx: 3, bbox: [100, 600, 900, 700], slot: "stem" };

// The lower half is saved as a part of the upper one, in the editor's order.
assert.equal(joinHost(bottom, [k2, bottom, top]), top);
assert.deepEqual(figuresFromBoxes([top, k2, bottom]), [
  { page_idx: 2, bbox: [100, 820, 900, 990], slot: "stem", candidate_key: "2:100,820,900,990",
    parts: [{ page_idx: 3, bbox: [100, 20, 900, 70], candidate_key: "3:100,20,900,70" }] },
  { page_idx: 3, bbox: [100, 600, 900, 700], slot: "stem" }
]);

// The joined piece follows the figure's slot, not its own stale one.
assert.equal(figuresFromBoxes([{ ...top, slot: "A" }, { ...bottom, slot: "stem" }])[0].slot, "A");

// Three pieces chain onto the first figure.
const third = { page_idx: 4, bbox: [100, 10, 900, 40], slot: "stem", join: true };
assert.equal(figuresFromBoxes([top, bottom, third])[0].parts.length, 2);

// Nothing before it: a "joined" piece stays its own figure instead of vanishing.
assert.equal(joinHost(bottom, [bottom]), null);
assert.deepEqual(figuresFromBoxes([bottom]).map((figure) => figure.parts), [undefined]);

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
assert.match(html, /data-figure-slot="join" aria-keyshortcuts="J"/);
assert.match(js, /j: "join" \}/);
// Opening the editor splits a stitched figure back into its pieces.
assert.match(js, /const joined = \(Array\.isArray\(f\.parts\) \? f\.parts : \[\]\)\.map\(\(part\) => \(\{\s*page_idx: part\.page_idx, bbox: \[\.\.\.part\.bbox\], slot: f\.slot, join: true/);
// Confirming the current figures from the card keeps the stitch.
assert.match(js, /Keep a stitched figure stitched when its figures are confirmed/);

console.log("figure join checks: OK");
