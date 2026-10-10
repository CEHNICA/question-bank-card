"use strict";

// 卡上那句「原卷第 N 题」是老师回原卷核对时要用的定位。
// 试卷的题号就是原卷先后；教材每一章的习题都从 1 重新编——九年级上册里就有
// 94 个「第 1 题」，光这句话翻书根本找不到是哪一章。章节名后端已经算好放在
// content.section，只是卡上没有显示。试卷的 section 是一整段大题说明，
// 放上去是噪音，所以只认短标签。
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

// 教材：章节名让「第 N 题」第一次真的能用。
assert.equal(scope.cardLocator({ number: 2, content: { section: "习题 6.1" } }), "习题 6.1 · 原卷第 2 题");
assert.equal(scope.cardLocator({ number: 1, content: { section: "复习题" } }), "复习题 · 原卷第 1 题");
// 试卷：大题说明那一整段不该出现在卡上。
const exam = { number: 8, content: { section: "一、选择题：本大题共8小题，每小题5分，共计40分." } };
assert.equal(scope.cardLocator(exam), "原卷第 8 题");
// 但短的大题名本身就是定位（纸上印着「三、解答题」），显示出来是对的。
assert.equal(scope.cardLocator({ number: 20, content: { section: "三、解答题" } }), "三、解答题 · 原卷第 20 题");
// 没有章节名、章节名是空白，都退回原来那句。
assert.equal(scope.cardLocator({ number: 5, content: {} }), "原卷第 5 题");
assert.equal(scope.cardLocator({ number: 5, content: { section: "   " } }), "原卷第 5 题");
assert.equal(scope.cardLocator({ number: 5 }), "原卷第 5 题");
// 卡上显示的必须是这个函数的结果，不能又写死一句。
assert.match(js, /node\("span", "", cardLocator\(item\)\)/);
