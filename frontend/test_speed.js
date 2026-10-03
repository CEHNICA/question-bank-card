"use strict";

// 1.10.3: a 600-card textbook opens quickly.  Cards off screen are laid out when
// they come near, the cards are found through a map, and the option lists are
// measured before any of them changes.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const QB = require("./qb-render.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8").replace(/\r\n/g, "\n");

// Cards off screen wait; the current card and one being edited never do.
assert.match(css, /content-visibility: auto; contain-intrinsic-size: auto 320px; \}/);
assert.match(css, /\.card\.compact \{ contain-intrinsic-size: auto 52px; \}/);
assert.match(css, /\.card\.is-current, \.card\.editing \{ content-visibility: visible; \}/);
// The card already clips its content, so waiting cannot hide a menu that used to show.
assert.match(css, /overflow: clip; transition: box-shadow \.18s, border-color \.18s;\n  \/\* 1\.10\.3/);

// One pass over the cards in the list, no selector search per card.
const render = js.slice(js.indexOf("function renderCards()"), js.indexOf("function cardNodes()"));
assert.match(render, /const existing = new Map\(\);/);
assert.match(render, /let card = existing\.get\(q\.id\) \|\| null;/);
assert.doesNotMatch(render, /container\.querySelector\(`\[data-id=/);

// fitOptions: read every size, then write; a list already right is left alone.
const reads = [];
const writes = [];
function list(width, cols, widest, initial) {
  const classes = new Set(initial);
  return {
    dataset: { cols: String(cols), widest: String(widest) },
    get clientWidth() { reads.push(writes.length); return width; },
    classList: {
      contains: (name) => classes.has(name),
      remove: (...names) => { writes.push("remove"); names.forEach((name) => classes.delete(name)); },
      add: (name) => { writes.push("add"); classes.add(name); },
    },
    classes,
  };
}
global.getComputedStyle = () => ({ fontSize: "16px" });
const wide = list(800, 4, 3, ["cols-4"]);       // four short options fit: unchanged
const narrow = list(300, 4, 12, ["cols-4"]);    // too narrow for four: two or one
const hidden = list(0, 4, 3, ["cols-4"]);       // not laid out: left alone
QB.fitOptions({ querySelectorAll: () => [wide, narrow, hidden] });
assert.ok(reads.every((count) => count === 0), "every list is measured before any class changes");
assert.deepEqual([...wide.classes], ["cols-4"]);
assert.equal(narrow.classes.has("cols-4"), false);
assert.ok(narrow.classes.has("cols-1") || narrow.classes.has("cols-2"));
assert.deepEqual([...hidden.classes], ["cols-4"]);
assert.equal(writes.length, 2, "only the list that changes is touched");

console.log("speed checks: OK");
