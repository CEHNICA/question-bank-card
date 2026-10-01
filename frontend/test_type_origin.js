"use strict";

// 1.10: an undecided type is chosen beside the question number (and the
// library's “题型待核对” links straight there); the source note (题源) is
// shown apart from the stem; a blank line before (1)(2) is not a paragraph.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const QB = require("./qb-render.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const library = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const libraryHtml = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// One more type everywhere: 判断题.
assert.equal(QB.TYPE_NAMES.true_false, "判断题");
assert.match(js, /true_false: "判断题"/);
assert.match(library, /\["true_false", "判断题"\]/);

// An undecided type blocks the tick, Enter and the viewer button, and each of them leads to the picker.
assert.match(js, /function typeBlocksApproval\(q\) \{\s*return Boolean\(q\.type_blocked\) && \(q\.state === "green" \|\| q\.state === "yellow"\);/);
assert.match(js, /function canApprove\(q\) \{\s*return Boolean\(q\.stem && !figureBlocksApproval\(q\) && !typeBlocksApproval\(q\)/);
assert.match(js, /else if \(typeBlocksApproval\(q\)\) focusTypePicker\(q\);/);
assert.match(js, /if \(typeBlocksApproval\(q\)\) \{ focusTypePicker\(q\); return; \}/);
assert.match(js, /typeBlocked \? "先选题型"/);
// Such a card counts as one to check, never as a green card waiting for a tick.
assert.match(js, /return !isApproved\(q\) && \(figureBlocksApproval\(q\) \|\| typeBlocksApproval\(q\) \|\|/);

// The picker sits in the card head and saves only the type.
assert.match(js, /head\.append\(el\("span", "qnum", questionLabel\(q\)\), typePicker\(q\), stateChip\(q\)\);/);
const picker = js.slice(js.indexOf("function typePicker(q)"), js.indexOf("function focusTypePicker(q)"));
assert.match(picker, /new Option\("题型未定 · 请选", "unknown"\)/);
assert.match(picker, /placeholder\.disabled = true;/);
assert.match(picker, /api\(`\/api\/questions\/\$\{q\.id\}\/type`, \{ method: "POST", body: \{ question_type: kind \} \}\)/);
assert.match(css, /\.qtype-select\.undecided \{/);
// Coming from the library with fix=type opens the picker.
assert.match(js, /params\.get\("fix"\) === "type" && typeBlocksApproval\(questionById\(draft\)\)/);
assert.match(library, /link\.href = draftLink\(item, "&fix=type"\);/);
assert.match(library, /"题型待核对 · 去选题型"/);

// 题源: its own field in 改字, its own line on the card, its own span in the library.
assert.match(js, /originRow\.append\(el\("span", "", "题源"\), origin\);/);
assert.match(js, /origin: origin\.value/);
assert.match(js, /const origin = el\("p", "card-origin"\);/);
assert.match(library, /node\("span", "library-origin", `题源：\$\{item\.origin\}`\)/);
// Printing the source is a choice, off by default.
assert.match(libraryHtml, /<input id="printOrigin" type="checkbox">/);
assert.match(library, /if \(ui\.printOrigin\.checked && origin\)/);

// A photo file name as the paper name gets a one-click rename.
assert.match(html, /id="renameNudge"[^>]*hidden/);
assert.match(js, /\$\("renameNudge"\)\.hidden = !looksLikeFileName\(paperDisplayName\(paper\)\);/);

// Feature switches live in settings and are read from the server.
assert.match(html, /id="featureSwitches"/);
assert.match(js, /api\("\/api\/settings\/features"\)/);

// A blank line before a sub-question is not a paragraph break; elsewhere it stays.
assert.equal(QB.tidyText(".\n\n(1)若命题"), ".\n(1)若命题");
assert.equal(QB.tidyText("；\n\n\n（2）若"), "；\n（2）若");
assert.equal(QB.tidyText("第一段\n\n第二段"), "第一段\n\n第二段");

console.log("type / origin checks: OK");
