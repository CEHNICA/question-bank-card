const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const ROOT = __dirname;

test("Eagle 工作台提供题目身份证一键复制与定位搜索支持，且题目 ID、来源、原卷位置与核验证据均位于考题属性中", () => {
  const html = fs.readFileSync(path.join(ROOT, "eagle", "index.html"), "utf8");
  assert.match(html, /id="inspectorLocatorRow"/);
  assert.match(html, /id="inspectorLocatorCode"/);
  assert.match(html, /id="btnCopyInspectorLocator"/);
  assert.match(html, /id="inspectorSourceRow"/);
  assert.match(html, /id="inspectorSourceText"/);
  assert.match(html, /id="inspectorPositionRow"/);
  assert.match(html, /id="inspectorPositionText"/);
  assert.match(html, /id="btnInspectorEvidence"/);
  assert.match(html, /function copyQuestionLocator\(/);
  assert.match(html, /function getQuestionShortId\(/);
  assert.match(html, /if \(sid && \(sid\.includes\(kw\) \|\| kw\.includes\(sid\)\)\) return true;/);
  const metaIdx = html.indexOf('id="questionMetaCard"');
  const locatorIdx = html.indexOf('id="inspectorLocatorRow"');
  const sourceIdx = html.indexOf('id="inspectorSourceRow"');
  const posIdx = html.indexOf('id="inspectorPositionRow"');
  assert.ok(metaIdx !== -1 && locatorIdx > metaIdx, "题目 ID 应位于考题属性卡片内");
  assert.ok(sourceIdx > metaIdx, "题目来源应位于考题属性卡片内");
  assert.ok(posIdx > metaIdx, "原卷位置与核验证据应位于考题属性卡片内");
});

test("AI 标签建议移除禁用排队死胡同，支持插队、取消、后台补标开关与一键就绪引导", () => {
  const html = fs.readFileSync(path.join(ROOT, "eagle", "index.html"), "utf8");
  assert.match(html, /AI 标签建议/);
  assert.doesNotMatch(html, /AI 语义推荐/);
  assert.match(html, /id="aiBgTagToggleBtn"/);
  assert.match(html, /async function toggleAutoTagBackground\(/);
  assert.match(html, /async function cancelTagSuggestionJob\(/);
  assert.match(html, /async function prioritizeTagSuggestionJob\(/);
  assert.match(html, /async function quickActivateEagleAi\(/);
  assert.match(html, /async function enableKnowledgeTagsAndGenerate\(/);
  assert.match(html, /id="aiTagCancelBtn"/);
  assert.match(html, /id="aiTagPriorityBtn"/);
  assert.match(html, /queueLibraryJob\('tags', qid, \{ retry: true, api_only: true, priority: 'high' \}\)/);
  assert.doesNotMatch(html, /⏳ 排队中…/);
});

test("统一 API 设置页移除网页端助手模式单选框、支持后台慢跑开关并提供一键沿用引导", () => {
  const apiJs = fs.readFileSync(path.join(ROOT, "api-settings.js"), "utf8");
  assert.doesNotMatch(apiJs, /apiUnifiedAnswerMode/);
  assert.doesNotMatch(apiJs, /AI 助手模式/);
  assert.match(apiJs, /无标签题目后台自动慢跑/);
  assert.match(apiJs, /auto_tag_background/);
  assert.match(apiJs, /findRecommendedAnswerService/);
  assert.match(apiJs, /api-quick-reuse-banner/);
});
