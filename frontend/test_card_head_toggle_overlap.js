"use strict";
// 录题/审核界面：展开态已通过题卡右上角「已入库」徽章与「收起」按钮禁止重合。
//
// 根因：展开态以前把 `.card-toggle` 绝对定位在卡片右上角，并依赖 `.card.has-toggle .card-head`
// 的 `padding-right: 84px` 让位；后续样式重写了 `.card-head` 的 `padding`（!important），
// 导致 `.card-head` 右侧的 `publicationChip` 与「收起」按钮直接叠在同一位置。
// 修复：与收起态 `.compact-row` 一致，把 `expandToggle(q, false)` 直接放进 `.card-head` flex 流中，
// 并在 CSS 中为 `.card-head .card-toggle` 声明 `position: static`。

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const stylesCss = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

test("展开态题卡头部收起按钮参与 flex 排版而不与已入库徽章重合", () => {
  const renderCardStart = appJs.indexOf("function renderCard(q) {");
  const renderCardEnd = appJs.indexOf("function readingSuggestionPanel(q) {");
  assert.ok(renderCardStart >= 0 && renderCardEnd > renderCardStart, "应能定位 renderCard 函数区间");
  const renderCardBody = appJs.slice(renderCardStart, renderCardEnd);

  const pubIdx = renderCardBody.indexOf('head.append(el("span", "head-spacer"), publicationChip(q));');
  const toggleIdx = renderCardBody.indexOf("if (approved && !isAiApproved(q)) head.append(expandToggle(q, false));");
  assert.ok(pubIdx >= 0, "卡片头部应追加 publicationChip");
  assert.ok(toggleIdx > pubIdx, "展开态收起按钮应在 publicationChip 之后直接追加到 head");
  assert.ok(!renderCardBody.includes("card.append(expandToggle(q, false))"), "展开态收起按钮不应直接挂在 card 下绝对定位");

  assert.match(
    stylesCss,
    /\.compact-row\s+\.card-toggle\s*,\s*\.card-head\s+\.card-toggle\s*\{[^}]*position:\s*static;/,
    "styles.css 应为 .card-head .card-toggle 声明 position: static"
  );
  assert.doesNotMatch(
    stylesCss,
    /\.card\.has-toggle\s+\.card-head\s*\{\s*padding-right:\s*84px;\s*\}/,
    "不应继续依赖脆弱的 .card.has-toggle .card-head padding-right 让位"
  );
});
