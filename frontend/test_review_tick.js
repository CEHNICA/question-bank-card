"use strict";

// 1.7.0: the square left of the question number is the reviewer's tick,
// not a delete selection; batch delete is an explicit mode.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// Every card, full or collapsed, starts with the approval tick.
assert.match(js, /function approvalTick\(q\)/);
assert.match(js, /row\.append\(approvalTick\(q\), cardSelectionControl\(q\)/);
assert.match(js, /head\.append\(approvalTick\(q\), cardSelectionControl\(q\)\)/);
// Ticking approves, ticking an approved card revokes, a figure block routes to the figure panel.
const tick = js.slice(js.indexOf("function approvalTick(q)"), js.indexOf("function handleCardSelectionClick"));
assert.match(tick, /tick\.setAttribute\("aria-pressed", byAi \? "mixed" : String\(approved\)\)/);
// 1.10: an undecided type routes to the type picker beside the number.
assert.match(tick, /if \(approved\) approveQuestion\(q, false\);\s*else if \(blocked\) focusFigureReview\(q\);\s*else if \(typeBlocked\) focusTypePicker\(q\);\s*else approveQuestion\(q, true\);/);
assert.match(tick, /event\.stopPropagation\(\)/, "ticking a collapsed row must not also expand it");
assert.match(tick, /tick\.disabled = !\(approved \|\| byAi \|\| blocked \|\| typeBlocked \|\| canApprove\(q\)\)/);
// A revoke by mistake is one click to undo.
assert.match(js, /\{ label: "恢复通过", onClick: \(\) => approveQuestion\(fresh, true, \{ advance: false \}\) \}/);

// Delete selection only shows in selection mode.
assert.match(css, /\.cards:not\(\.selecting\) \.card-select, \.cards\.selecting \.card-tick \{ display: none; \}/);
assert.match(css, /\.card-select \{ border-radius: 50%; \}/);
assert.match(css, /\.card-tick\[aria-pressed="true"\]/);
assert.match(html, /id="selectionStart"[^>]*>批量删除题卡…<\/button>/);
assert.match(html, /id="selectionCancel"[^>]*>完成<\/button>/);
assert.match(js, /function startSelecting\(\)/);
assert.match(js, /function stopSelecting\(\{ render = true \} = \{\}\)/);
assert.match(js, /bar\.hidden = !state\.selecting;/);
assert.match(js, /\$\("cards"\)\.classList\.toggle\("selecting", state\.selecting\)/);
assert.match(js, /\$\("selectionStart"\)\.addEventListener\("click", \(\) => \{ \$\("toolsMenu"\)\.open = false; startSelecting\(\); \}\)/);
// Ctrl/Shift-click still works and switches the mode on.
const select = js.slice(js.indexOf("function selectQuestion(q, event = {})"), js.indexOf("function cardSelectionControl"));
assert.match(select, /state\.selecting = true;/);
// Esc, 完成, switching paper and a finished delete all leave the mode.
assert.match(js, /case "Escape":\s*if \(state\.selecting && !state\.selectionBusy\) \{ event\.preventDefault\(\); stopSelecting\(\); \}/);
assert.match(js, /\$\("selectionCancel"\)\.addEventListener\("click", \(\) => stopSelecting\(\)\)/);
assert.match(js, /if \(state\.paperId !== id\) \{\s*stopSelecting\(\{ render: false \}\);/);
assert.match(js, /state\.editing\.delete\(id\); \}\);\s*stopSelecting\(\{ render: false \}\);/);

// The toast sits at the right, clear of the left-aligned card buttons.
assert.match(css, /\.toast\[popover\] \{ inset: auto 24px 78px auto;/);
assert.doesNotMatch(css, /\.toast \{[^}]*translateX\(-50%\)/);

// The shortcut sheet tells the new meaning.
assert.match(html, /打勾通过 \/ 再点撤销/);

// The tick is the one place to approve: no second 标记通过 / 撤销通过 button on the card.
assert.doesNotMatch(js, /button\(blocked \? blockedLabel : approvalNeedsReview\(q\) \? "重新标记通过" : "标记通过"/);
assert.doesNotMatch(js, /actions\.append\(button\("撤销通过"/);
console.log("review tick and selection mode checks: OK");
