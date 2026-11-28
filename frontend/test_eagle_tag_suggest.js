"use strict";
// 联想打标：绿条落在哪一条就贴哪一条；没有题目再用的自建标签不进联想。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { describe, it } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function extractBetween(startNeedle, endNeedle) {
  const start = html.indexOf(startNeedle);
  const end = html.indexOf(endNeedle, start + startNeedle.length);
  assert.ok(start >= 0 && end > start, "应能抽出 " + startNeedle);
  return html.slice(start, end);
}

describe("标签联想", () => {
  it("绿条优先于输入框里的原文", () => {
    const sandbox = {};
    vm.runInNewContext(
      extractBetween("function pickTagSuggestTarget", "async function commitTagSuggest") +
        "\nthis.pickTagSuggestTarget = pickTagSuggestTarget;",
      sandbox
    );
    const parallel = { id: "short", name: "平行" };
    const parallelogram = { id: "long", name: "平行四边形" };
    const picked = sandbox.pickTagSuggestTarget("平行", [parallel, parallelogram], 1);
    assert.equal(picked.kind, "match");
    assert.equal(picked.tag.id, "long");
    const exact = sandbox.pickTagSuggestTarget("平行", [parallel, parallelogram], -1);
    assert.equal(exact.tag.id, "short");
  });

  it("没有题目再用的自建标签不进入联想，知识点即使还没贴过也保留", () => {
    const sandbox = { questionsData: [] };
    vm.runInNewContext(
      extractBetween("function tagIsUnusedCustom", "function computeTagSuggestions") +
        "\nthis.tagIsUnusedCustom = tagIsUnusedCustom;",
      sandbox
    );
    assert.equal(sandbox.tagIsUnusedCustom({ id: "1", name: "平行", kind: "custom", count: 0 }), true);
    assert.equal(sandbox.tagIsUnusedCustom({ id: "2", name: "平行四边形", kind: "knowledge", count: 0 }), false);
    assert.equal(sandbox.tagIsUnusedCustom({ id: "1", name: "平行", kind: "custom", count: 2 }), false);
    sandbox.questionsData = [{ tags: [{ id: "1" }] }];
    assert.equal(sandbox.tagIsUnusedCustom({ id: "1", name: "平行", kind: "custom", count: 0 }), false);
  });

  it("没输入时不列出标签，输入后才按名字联想", () => {
    const sandbox = {
      questionsData: [],
      currentActiveQuestionId: null,
      tagCatalogue: [
        { id: "1", name: "倍长中线", kind: "method", active: true, count: 0 },
        { id: "2", name: "比例尺与相似比", kind: "knowledge", active: true, count: 5, favorite: true },
        { id: "3", name: "向量", kind: "knowledge", active: true, count: 9 },
      ],
    };
    vm.runInNewContext(
      "const TAG_SUGGEST_MAX = 12;\n" +
        extractBetween("function currentQuestionTagIds", "function renderTagSuggestDropdown") +
        "\nthis.computeTagSuggestions = computeTagSuggestions;",
      sandbox
    );
    assert.equal(sandbox.computeTagSuggestions("").length, 0);
    assert.equal(sandbox.computeTagSuggestions("   ").length, 0);
    const typed = sandbox.computeTagSuggestions("比例");
    assert.equal(typed.length, 1);
    assert.equal(typed[0].id, "2");
    const exact = sandbox.computeTagSuggestions("向量");
    assert.equal(exact[0].id, "3");
  });

  it("空输入框不展开「没有可用标签」", () => {
    const render = extractBetween("function renderTagSuggestDropdown", "function hideTagSuggestDropdown");
    assert.doesNotMatch(render, /没有可用标签/);
    assert.match(render, /if \(!trimmed\)/);
    assert.match(render, /classList\.add\('hidden'\)/);
  });

  it("点下拉时按标签编号贴，看题右栏不放置顶", () => {
    assert.match(html, /data-tag-id="/);
    assert.match(html, /pickTagSuggestTarget\(query, tagSuggestMatches, tagSuggestHighlight\)/);
    assert.doesNotMatch(html, /id="pinnedTagsSection"/);
    assert.doesNotMatch(html, />常用</);
    assert.match(html, /scheduleUnusedCustomTagSweep\(\)/);
  });
});
