"use strict";

// 卡上那句定位是老师回原卷核对时唯一能用的话。
// 试卷的题号就是原卷先后；教材每一章的习题都从 1 重新编——九年级上册里就有
// 94 个「第 1 题」，光这句话翻书根本找不到是哪一章。章节名后端已经算好放在
// content.section。试卷的 section 是一整段大题说明，放上去是噪音，所以只认短标签。
//
// 后端 library.section_label 认的是同一条规则——它决定哪些章节名进搜索，
// 这边决定哪些章节名印上卡面。同一段文字一处印了、一处搜不到，老师立刻就发现。
// 所以两边读同一份 testdata/section_labels.json；取值全部来自真库 112 种 section。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const start = js.indexOf("  function cardLocator(item)");
const end = js.indexOf("  function card(item)");
assert.ok(start > 0 && end > start, "cardLocator 必须存在且排在 card() 前面");
const scope = {};
vm.runInNewContext(js.slice(start, end), scope);

const fixture = JSON.parse(
  fs.readFileSync(path.join(__dirname, "testdata", "section_labels.json"), "utf8"));
assert.ok(fixture.cases.length >= 10, "fixture 至少要覆盖教材短标签、试卷长说明和 12/13 字边界");

for (const { section, label } of fixture.cases) {
  const number = 8;
  const expected = label ? `${label} · 原卷第 ${number} 题` : `原卷第 ${number} 题`;
  assert.equal(
    scope.cardLocator({ number, content: { section } }), expected,
    `章节名 ${JSON.stringify(section)} 的卡面定位应当是 ${JSON.stringify(expected)}`);
}

// 卡面上真正显示的，必须是这个函数的结果，不能又写死一句。
assert.match(js, /node\("span", "", cardLocator\(item\)\)/);

// 卡上没给 content 也照样退回「原卷第 N 题」。
assert.equal(scope.cardLocator({ number: 5 }), "原卷第 5 题");
