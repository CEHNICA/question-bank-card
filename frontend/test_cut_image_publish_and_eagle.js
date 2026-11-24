"use strict";
// 切割原图题卡直接入库与 Eagle 题库闭环回归测试。

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const indexHtml = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");
const unifiedEditorJs = fs.readFileSync(path.join(__dirname, "library-unified-editor.js"), "utf8");

test("录题页提供独立的原图批量入库入口与 MinerU 只切题选项", () => {
  assert.match(indexHtml, /id="publishCutImages"/);
  assert.match(indexHtml, /id="uploadCutOnly"/);
  assert.match(indexHtml, /id="photoCutOnly"/);

  assert.match(appJs, /function eligibleCutImageCards\(/);
  assert.match(appJs, /async function publishCutImages\(/);
  assert.match(appJs, /\/api\/papers\/\$\{paperId\}\/publish-cut-images/);
  assert.match(appJs, /if \(policy\?\.cutOnly === true\) form\.append\("cut_only", "1"\);/);
});

test("Eagle 题库支持题型未定、原图筛选、空题干编辑保存与 AI 转文字升级", () => {
  assert.match(eagleHtml, /unknown:\s*'题型未定'/);
  assert.match(eagleHtml, /\{\s*code:\s*'unknown',\s*label:\s*'题型未定'\s*\}/);
  assert.match(eagleHtml, /id="btnBodySourceImage"/);
  assert.match(eagleHtml, /id="btnBodyText"/);
  assert.match(eagleHtml, /function toggleBodyModeFilter\(/);
  assert.match(eagleHtml, /q\.isSourceImage = nextSourceImg/);
  assert.match(unifiedEditorJs, /if \(qDraft\.body_mode === "text" && !qDraft\.stem\.trim\(\)\)/);
  assert.match(eagleHtml, /async function ocrUpgradeQuestion\(/);
  assert.match(eagleHtml, /async function batchOcrUpgrade\(/);
  assert.match(eagleHtml, /\/api\/library\/ocr-upgrade-batch/);
  assert.match(eagleHtml, /function startOcrUpgradePolling\(/);
  assert.match(eagleHtml, /async function pollPendingOcrUpgrades\(/);
});

test("录入前未定题型引导、手工切题记忆题型与快捷键、导出混排及内联定型接线完整", () => {
  const layoutJs = fs.readFileSync(path.join(__dirname, "original-paper-layout.js"), "utf8");
  const examExport = require("./exam-export.js");

  assert.match(layoutJs, /const TYPE_SHORTCUTS = \{/);
  assert.match(layoutJs, /const rememberedTypeByPaper = new Map\(\)/);
  assert.match(appJs, /function resolveCutImageTypesBeforePublish\(/);
  assert.match(eagleHtml, /id="exportGroupMode"/);
  assert.match(eagleHtml, /async function saveBasketQuestionType\(/);
  assert.match(eagleHtml, /async function applyBasketBulkQuestionType\(/);

  const items = [
    { id: "q-unknown", question_type: "unknown", content: { stem: "未定", section: "" } },
    { id: "q-choice", question_type: "single_choice", content: { stem: "选择", section: "" } },
  ];

  // 默认按题型归组：选择题在前，未定归「其他」
  const byType = examExport.paperGroups(items, { group_mode: "by_type" });
  assert.deepEqual(
    byType.map(([name, group]) => [name, group.map((x) => x.id)]),
    [["选择题", ["q-choice"]], ["其他", ["q-unknown"]]]
  );
  assert.equal(examExport.sectionHeading(byType[1][1], 1, "其他"), "二、其他");

  // 按选题顺序混排：保留输入顺序且不印「其他」大题头
  const basketOrder = examExport.paperGroups(items, { group_mode: "basket_order" });
  assert.equal(basketOrder.length, 1);
  assert.equal(basketOrder[0][0], null);
  assert.deepEqual(basketOrder[0][1].map((x) => x.id), ["q-unknown", "q-choice"]);
  assert.deepEqual(examExport.paperOrder(items, { group_mode: "basket_order" }), ["q-unknown", "q-choice"]);
  assert.equal(examExport.sectionHeading(basketOrder[0][1], 0, null), "");
});
