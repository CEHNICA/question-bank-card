"use strict";

// 1.10.5: 入库 goes in batches of 20 and the button says how far it got, so a
// 200-card book no longer looks stuck.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const publish = js.slice(js.indexOf("async function publish()"), js.indexOf('$("publishButton").addEventListener("click", publish);'));

assert.match(js, /const PUBLISH_BATCH = 20;/);
// Only the cards not yet in the library at this version, in batches.
assert.match(publish, /state\.questions\.filter\(\(q\) => isApproved\(q\) && !\(q\.publication && q\.publication\.up_to_date\)\)/);
assert.match(publish, /body: batch \? \{ question_ids: batch \} : \{\}/);
// One click at a time; the count moves after every batch.
assert.match(publish, /if \(state\.publishing\) return;/);
assert.match(publish, /state\.publishing\.done \+= batch \? batch\.length : 0;/);
assert.match(js, /button\.textContent = `正在入库 \$\{running\.done\} \/ \$\{running\.total\} 题…`;/);
// A failure part-way says what did go in.
assert.match(publish, /已入库 \$\{data\.created\} 题，其余没完成/);
// renderPaper does not overwrite the progress.
assert.match(js, /renderPublishButton\(c, structureBlocked\);/);
assert.doesNotMatch(js, /\$\("publishButton"\)\.textContent = c\.unpublished/);
// Busy, not disabled-looking: a turning ring, still at full colour.
assert.match(css, /\.button\.is-busy:disabled \{ opacity: 1; cursor: progress;/);
assert.match(css, /\.button\.is-busy::before \{[^}]*animation: spin/);
assert.match(css, /prefers-reduced-motion: reduce\) \{ \.button\.is-busy::before \{ animation: none;/);

console.log("publish progress checks: OK");
