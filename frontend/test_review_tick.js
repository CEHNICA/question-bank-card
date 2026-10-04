"use strict";

// 1.7.0: the square left of the question number is the reviewer's tick.
// 1.12.5: 批量处理题卡（选择模式 + 批量识读/批量删除）整套删除，题号左边
// 只剩这一个打勾方框，不再有会跟它混淆的圆形选择框。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// Every card, full or collapsed, starts with the approval tick.
assert.match(js, /function approvalTick\(q\)/);
assert.match(js, /row\.append\(approvalTick\(q\), el\("span", "qnum"/);
assert.match(js, /head\.append\(approvalTick\(q\)\)/);
// Ticking approves; ticking an already-ticked card revokes, a figure block routes to the figure panel.
// 1.12.6: the tick is one reversible switch.  A card the library already holds is
// ticked too, even when nobody ever ticked that card by hand — otherwise the box
// looked empty and clicking it looked broken.
const tick = js.slice(js.indexOf("function approvalTick(q)"), js.indexOf("function renderCards()"));
assert.match(tick, /const published = isSettled\(q\);/);
assert.match(tick, /const ticked = approved \|\| byAi \|\| published;/);
assert.match(tick, /tick\.setAttribute\("aria-pressed", byAi \? "mixed" : String\(ticked\)\)/);
assert.match(tick, /published && !approved \? " published" : ""/);
// 1.10: an undecided type routes to the type picker beside the number.
assert.match(tick, /if \(ticked\) revokeQuestion\(q\);\s*else if \(blocked\) focusFigureReview\(q\);\s*else if \(typeBlocked\) focusTypePicker\(q\);\s*else approveQuestion\(q, true\);/);
assert.match(tick, /event\.stopPropagation\(\)/, "ticking a collapsed row must not also expand it");
assert.match(tick, /tick\.disabled = !\(ticked \|\| blocked \|\| typeBlocked \|\| canApprove\(q\)\)/);
// The tick must say what clicking it will do to the library copy.
assert.match(tick, /取消勾会同时从题库撤回/);
// Revoking clears the approval AND withdraws the library version: two requests,
// and the second one is a different endpoint.  Without it the library record
// stayed and the reviewer saw nothing happen.
const revoke = js.slice(js.indexOf("async function revokeQuestion(q"), js.indexOf("function focusNext(q)"));
assert.match(revoke, /method: "POST", body: \{ approved: false \}/);
// 撤回必须带一个空 JSON 体。原来写的是 `{ method: "POST" }`，服务端因为没有
// Content-Type 挡成「请求格式不正确」——请求发出去了、失败提示也弹了，但题库里
// 那一版留在原地，界面看起来像撤了其实没撤。只看对号的 DOM 属性测不出来，
// 必须真的点一次、看题库条数。
assert.match(revoke, /`\/api\/library\/\$\{live\.id\}\/withdraw`, \{ method: "POST", body: \{\} \}/);
assert.doesNotMatch(revoke, /withdraw`, \{ method: "POST" \}/);
assert.match(revoke, /await refreshPaper\(\)/, "the withdraw endpoint returns no paper, so counts need a reload");
assert.doesNotMatch(revoke, /catch \(error\) \{ \},/);
// 撤回失败的那一步必须自己说出来，不能被后面那句成功提示盖掉。
assert.match(revoke, /但没能从题库撤回：\$\{error\.message\}。/);
assert.match(revoke, /已撤销第 \$\{q\.number\} 题的通过并从题库撤回/);
// A revoke by mistake is one click to undo.
assert.match(js, /\{ label: "恢复通过", onClick: \(\) => approveQuestion\(fresh, true, \{ advance: false \}\) \}/);
// No caller may un-approve behind revokeQuestion's back: that is the old bug.
assert.doesNotMatch(js, /approveQuestion\([^)]*,\s*false/);
assert.match(css, /\.card-tick\.published \{/);

// The tick is the only square left of the number: the batch-select round one
// is gone from markup, styles and script alike.
assert.match(css, /\.card-tick\[aria-pressed="true"\]/);
assert.doesNotMatch(html, /id="selectionStart"/);
assert.doesNotMatch(html, /id="selectionBar"/);
assert.doesNotMatch(html, /id="selectionCancel"/);
assert.doesNotMatch(html, /id="selectionReread"/);
assert.doesNotMatch(html, /id="selectionDelete"/);
assert.doesNotMatch(css, /card-select|selection-bar|is-selected/);
assert.doesNotMatch(js, /startSelecting|stopSelecting|renderSelectionState|cardSelectionControl|updateSelection/);
assert.doesNotMatch(js, /state\.selected|state\.selecting|state\.selectionAnchor/);
// Esc still leaves full-screen review.
assert.match(js, /case "Escape":\s*if \(document\.documentElement\.classList\.contains\("review-fullscreen"\)\) \{ event\.preventDefault\(\); setReviewFullscreen\(false\); \}/);
const paperChange = js.slice(js.indexOf("  async function selectPaper(id)"), js.indexOf("  async function clearPaperSelection()"));
assert.ok(paperChange.indexOf("cancelPendingPageOpening();") < paperChange.indexOf("state.paperId = id;"), "Paper navigation invalidates an old manual opening before selecting another paper");

// The toast sits at the right, clear of the left-aligned card buttons.
assert.match(css, /\.toast\[popover\] \{ inset: auto 24px 78px auto;/);
assert.doesNotMatch(css, /\.toast \{[^}]*translateX\(-50%\)/);

// The shared shortcut sheet explains that Enter continues; undo remains explicit.
const help = require("./shortcut-help.js").reference("review");
assert.ok(help.primary.some(row => row.label === "通过并继续" && row.keys.includes("Enter")));
assert.match(help.extra, /Enter 不撤销已通过的题/);
assert.ok(help.more.some(row => row.label === "撤销当前题通过" && row.keys.includes("U")));
assert.ok(!help.more.some(row => /批量选择|移到回收站/.test(row.label)), "批量选择的快捷键说明已随功能一起删除");

// The tick is the one place to approve: no second 标记通过 / 撤销通过 button on the card.
assert.doesNotMatch(js, /button\(blocked \? blockedLabel : approvalNeedsReview\(q\) \? "重新标记通过" : "标记通过"/);
assert.doesNotMatch(js, /actions\.append\(button\("撤销通过"/);
console.log("review tick checks: OK");
