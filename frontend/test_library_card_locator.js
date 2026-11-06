"use strict";

// 卡上那两句话是老师回原卷核对时唯一能用的话。
//
// 1.26.43 起全库只留一套说法（frontend/question-locator.js）：
//   大标题说「这是第几题」（identityOf），小字说「在书上哪一页哪一节」（positionOf）。
// 页码是唯一的硬锚点 —— 同一本教材里章节名（「练习」「思考」）和题号都会重号，
// 只有「书上第几页」认得出来，所以每一张卡都要带页码。
//
// 章节名的判定三处共用一条规矩（后端 library.section_label、导出用的
// ExamExport.shortSectionLabel、定位模块），都读同一份 testdata/section_labels.json。
// 「第 N 组」是位置标签，不是章节名，不许印上卡面。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const L = require("./question-locator.js");
const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

// 卡面上的话必须由统一模块说出来，library.js 自己不许另拼一句。
assert.match(js, /window\.QBQuestionLocator\?\.identityOf\(item\)/, "卡头的话由统一模块负责");
assert.match(js, /window\.QBQuestionLocator\?\.positionOf\(item\)/, "卡上小字的话由统一模块负责");

// 整份 library.js 不得再直接拿题号拼一句「…第 N 题」。
const direct = [...js.matchAll(/\$\{(?:item|known|question)\.number\} 题/g)];
assert.equal(
  direct.length,
  0,
  `library.js 里还有直接拼题号的地方：${direct.map((m) => js.slice(Math.max(0, m.index - 60), m.index + 20).replace(/\s+/g, " ")).join(" | ")}`
);

// 卡上真正显示的，就是模块算出来的那一句。
const start = js.indexOf("  function sourceLocator(item)");
const end = js.indexOf("  function card(item)");
assert.ok(start > 0 && end > start, "sourceLocator/cardLocator 必须存在且排在 card() 前面");
const scope = { window: { QBQuestionLocator: L } };
vm.runInNewContext(js.slice(start, end), scope);
assert.equal(typeof scope.sourceLocator, "function");
assert.equal(typeof scope.cardLocator, "function");

// 卡头：这是第几题。
assert.equal(scope.sourceLocator({ number: 8 }), "第 8 题");
assert.equal(scope.sourceLocator({ number: null, content: { section: "做一做" } }), "做一做");
assert.equal(scope.sourceLocator({}), "栏目题");

// 卡上小字：书上第几页、哪一节。
assert.equal(scope.cardLocator({ number: 8, page: 42 }), "第 42 页");
assert.equal(scope.cardLocator({ number: 8, page: 42, content: { section: "习题 6.1" } }), "第 42 页 · 习题 6.1");
assert.equal(scope.cardLocator({ number: 8 }), "");

// 每一张卡都要带页码 —— 老师翻书靠的是页码，不是题号。
const cardStart = js.indexOf("function card(item)");
const cardEnd = js.indexOf("\n  function ", cardStart + 10);
assert.ok(cardStart > 0 && cardEnd > cardStart, "card() 必须能被切出来");
const cardBody = js.slice(cardStart, cardEnd);
assert.match(cardBody, /provenance\.append\(node\("span", "", position\)\)/, "卡上要印出书上第几页");

// 光看代码里有没有那句话不算数 —— 得看它是不是真的挂到卡上了。
// 把挂定位小字的那几行原样抠出来，在假 DOM 里真跑一遍，读挂上去的节点。
const mount = cardBody.match(
  /const position = cardLocator\(item\);[\s\S]*?provenance\.append\(node\("span", "", position\)\);/
);
assert.ok(mount, "卡上要真的把定位句挂到 provenance 上");
const makeNode = (tag, className, text) => ({ tag, className, text: text === undefined || text === null ? "" : String(text) });
const mounted = [];
vm.runInNewContext(mount[0], {
  item: { number: 8, page: 42 },
  cardLocator: scope.cardLocator,
  node: makeNode,
  provenance: { append: (kid) => mounted.push(kid) }
});
assert.equal(mounted.length, 1, "卡上要真的印出那一句定位");
assert.equal(mounted[0].text, "第 42 页", "卡上小字说的是书上第几页");

// 认不出页码时不留空行。
const emptyMounted = [];
vm.runInNewContext(mount[0], {
  item: { number: 8 },
  cardLocator: scope.cardLocator,
  node: makeNode,
  provenance: { append: (kid) => emptyMounted.push(kid) }
});
assert.equal(emptyMounted.length, 0, "认不出页码时卡上不留空行");
assert.doesNotMatch(
  cardBody,
  /if \(shortSection\(item\)\) provenance\.append\(/,
  "定位句不再只在有章节名时才显示 —— 页码每张卡都要有"
);

// 卡头是三列 grid：18px（复选框）| auto（题号+题型）| minmax(0,1fr)（文件名）。
// 一句没有类名的定位如果作为卡头的直接子元素，会被自动排进那个 18px 的窄槽，
// 每个字换一行，竖成一列——实机量到过 18px 宽 × 108px 高。
assert.doesNotMatch(
  cardBody,
  /meta\.append\([^)]*node\("span", "", /,
  "定位句不能作为没有类名的直接子元素挂到卡头上（会被挤成一竖列）"
);

// ---- 章节名的判定：三处同一条规矩 ----
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "testdata", "section_labels.json"), "utf8"));
assert.ok(fixture.cases.length >= 10, "fixture 至少要覆盖教材短标签、试卷长说明和 12/13 字边界");
const ExamExport = require("./exam-export.js");
for (const { section, label } of fixture.cases) {
  assert.equal(L.sectionLabel(section), label, `章节名 ${JSON.stringify(section)} 的判定应当是 ${JSON.stringify(label)}`);
  assert.equal(ExamExport.shortSectionLabel(section), label, "导出那边认的是同一条规矩");
}

// 「第 N 组」这种位置标签不是章节名，不许印上卡面。
assert.equal(L.scopeOf({ content: { source_group: { title: "第 3 组" } } }), "");
assert.equal(L.scopeOf({ content: { source_group: { title: "第 12 页 · 第 3 组" } } }), "");
assert.equal(L.scopeOf({ content: { source_group: { title: "章节02 · 配方法" } } }), "章节02 · 配方法");

// 没有印刷题号的教材栏目题，定位照样要带页码。
assert.equal(scope.cardLocator({ number: null, page: 42, content: { section: "做一做" } }), "第 42 页");
assert.equal(scope.sourceLocator({ number: null, content: { section: "做一做" } }), "做一做");

console.log("library card locator: ok");
