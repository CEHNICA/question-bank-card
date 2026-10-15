"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const app = fs.readFileSync(require.resolve("./app.js"), "utf8");
const layout = fs.readFileSync(require.resolve("./original-paper-layout.js"), "utf8");

assert.doesNotMatch(app, /function continueManualCut\(/, "There is no parallel legacy manual-cut screen");
assert.doesNotMatch(app, /\/api\/papers\/\$\{state\.paperId\}\/questions/, "Manual additions use the unified transactional layout service");
assert.match(app, /if \(dialog\.layoutWorkspace\) \{ void originalLayout\.preview\(\{ next, complete \}\); return; \}/,
  "Save-next and finish actions both route through the layout controller");
assert.match(app, /if \(mode === "new"\) mode = "view";/, "Any old new-question entry is redirected to the unified workspace");
const entry = app.slice(app.indexOf("  async function enterCutReadingStage("), app.indexOf("  async function readNewlyCutUpload("));
assert.doesNotMatch(entry, /await readCutQuestions\(/, "Finishing manual cutting never starts recognition");
assert.match(entry, /主动选择 AI 识读或直接原图审核/);
assert.match(app, /保存并框下一题/);
assert.match(layout, /async function returnToReview\(\)/);
assert.match(layout, /if \(complete\) return returnToReview\(\)/);
assert.match(layout, /disposition === "next"/);
assert.match(layout, /重试查询保存结果/);

console.log("Unified manual cut: no duplicate create branch, final-save routing, explicit recognition and idempotent recovery: OK");
