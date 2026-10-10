"use strict";

// 卡上那句定位是老师回原卷核对时唯一能用的话。
// 试卷的题号就是原卷先后；教材每一章的习题都从 1 重新编——九年级上册里就有
// 94 个「第 1 题」，光这句话翻书根本找不到是哪一章。章节名后端已经算好放在
// content.section。试卷的 section 是一整段大题说明，放上去是噪音，所以只认短标签。
//
// 后端 library.section_label 认的是同一条规则——它决定哪些章节名进搜索，
// 这边决定哪些章节名印上卡面。同一段文字一处印了、一处搜不到，老师立刻就发现。
// 所以两边读同一份 testdata/section_labels.json；取值全部来自真库 112 种 section。
//
// 教材「做一做」「随堂练习」里的题纸面上没有印刷题号，后端给 number=null。
// 这种卡如果还按「第 N 题」拼，印出来的是「第 0 题」或「第 undefined 题」——
// 翻书翻不到的一句话比没有更糟。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const start = js.indexOf("  function shortSection(item)");
const end = js.indexOf("  function card(item)");
assert.ok(start > 0 && end > start, "shortSection/cardLocator/sourceLocator 必须存在且排在 card() 前面");
const scope = {};
vm.runInNewContext(js.slice(start, end), scope);
assert.equal(typeof scope.shortSection, "function");
assert.equal(typeof scope.sourceLocator, "function");

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

// ---- 没有印刷题号的教材栏目题 ----

// 栏目标题就是纸面上唯一翻得到的定位，直接用它。
assert.equal(scope.cardLocator({ number: null, content: { section: "做一做" } }), "做一做");
assert.equal(scope.cardLocator({ number: null, content: { section: "随堂练习" } }), "随堂练习");

// 0 是切题时给占位用的旧值，也不能印成「第 0 题」。
assert.equal(scope.cardLocator({ number: 0, content: { section: "做一做" } }), "做一做");
assert.equal(scope.sourceLocator({ number: 0, content: { section: "做一做" } }), "做一做");

// 连栏目名也没有时也不能出现「0」「null」「undefined」。
for (const bad of [null, undefined, 0]) {
  const card = String(scope.cardLocator({ number: bad }));
  const loc = String(scope.sourceLocator({ number: bad }));
  assert.ok(!/\b0 题/.test(card) && !card.includes("null") && !card.includes("undefined"),
    `没有题号时卡面不能出现「第 0 题」这类纸面上不存在的话：${card}`);
  assert.ok(!/\b0 题/.test(loc) && !loc.includes("null") && !loc.includes("undefined"),
    `没有题号时定位不能出现「第 0 题」：${loc}`);
}

// 长说明（大题标题）不该被当成定位标签用，但也不能编出题号。
const noisy = scope.cardLocator({ number: null, content: { section: "一、选择题：本大题共8小题，每小题5分。" } });
assert.ok(!noisy.includes("0 题"), noisy);

// 有题号的卡，定位仍是「第 N 题」。
assert.equal(scope.sourceLocator({ number: 12 }), "第 12 题");
assert.equal(scope.sourceLocator({ number: 12, content: { section: "习题 6.1" } }), "第 12 题");

// ---- 整份 library.js 不得再直接拿题号拼一句「…第 N 题」 ----

// sourceLocator / cardLocator 内部那两处是唯一允许的：都在
// `if (item?.number)` 之后，没有题号时根本走不到。先把这两段摘掉再查剩下的。
const guarded = js.slice(start, end);
const rest = js.slice(0, start) + js.slice(end);
const direct = [...rest.matchAll(/\$\{(?:item|known)\.number\} 题/g)];
assert.equal(direct.length, 0,
  `library.js 里还有直接拼题号的地方：${direct.map((m) => rest.slice(Math.max(0, m.index - 50), m.index + 20).replace(/\s+/g, " ")).join(" | ")}`);
assert.match(guarded, /if \(item\?\.number\) return `第 \$\{item\.number\} 题`/,
  "sourceLocator 必须先判题号再拼，否则无题号的栏目题会印出「第 0 题」");