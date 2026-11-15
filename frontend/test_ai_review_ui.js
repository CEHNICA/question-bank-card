"use strict";

// Cards an AI assistant passed (tiyouju / MCP) count as passed but stay
// visibly separate until a person confirms them.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

const review = { state: { cropDraftAttention: new Map() } };
vm.runInNewContext(js.slice(js.indexOf("  function approvalNeedsReview(q)"), js.indexOf("  function anyDialogOpen()")), review);
const aiQuestion = { id: 1, approved: true, approved_by: "ai", state: "green" };
assert.equal(review.isAiApproved(aiQuestion), true);
assert.equal(review.isHumanApproved(aiQuestion), false);
assert.equal(review.isAiApproved({ ...aiQuestion, approved_by: "human" }), false);
assert.equal(review.isHumanApproved({ ...aiQuestion, approved_by: "human" }), true);
assert.equal(review.isAiApproved({ ...aiQuestion, approval_valid: false }), false);
// Its own chip, a dashed tick, a dashed bar; it does not fold away.
assert.match(js, /el\("span", "chip ai-approved", `\$\{agentLabel\(q\)\} 已通过 · 待你核对`\)/);
// 1.12.6: the tick gained a second dashed state for "题库里已经放着、但我还没亲自
// 打过勾"，so the class list is no longer a plain ai-or-nothing ternary.
assert.match(js, /el\("button", `card-tick\$\{byAi \? " ai" : ""\}\$\{published && !approved \? " published" : ""\}`\);/);
assert.match(js, /tick\.setAttribute\("aria-pressed", byAi \? "mixed" : String\(ticked\)\);/);
assert.match(js, /const approvedCompact = approved && !isAiApproved\(q\) && !state\.expanded\.has\(q\.id\);/);
assert.match(css, /\.card-tick\.ai \{ border: 1\.5px dashed var\(--accent\);/);
assert.match(css, /\.card\.state-ai::before \{ background: repeating-linear-gradient/);
// Clicking the tick, Enter, or the viewer button turns it into the person's approval.
assert.match(js, /else if \(isAiApproved\(q\) \|\| canApprove\(q\)\) approveQuestion\(q, true\);/);
assert.match(js, /approve\.replaceChildren\(icon\("check"\), document\.createTextNode\("确认通过并下一题"\)/);
assert.match(js, /`已确认第 \$\{q\.number\} 题（原来是 \$\{agentLabel\(q\)\} 通过）`/);
// Next card to review includes the ones only an AI passed.
assert.match(js, /visible\(q\) && !isHumanApproved\(q\) && \(!onlyCheck \|\| needsCheck\(q\)\)/);
// An “AI 通过” filter appears only when there is something in it.
assert.match(js, /\{ key: "ai", label: "AI 通过", optional: true \}/);
assert.match(js, /if \(filter\.optional && !c\[filter\.key\] && !active\) return;/);
assert.match(js, /if \(state\.filter === "ai"\) return isAiApproved\(q\);/);

// 旧「正式题库」页的「谁审核的」筛选已随该页下线；题库工作台不搬审核流。

console.log("AI review UI checks: OK");
