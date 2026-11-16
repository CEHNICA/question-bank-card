"use strict";

// 右侧详情栏里的「为本题写答案」必须够得着。
//
// 1.25.76 起的意图很明确：缺答案的题，右侧面板直接给一条「跳答案编辑器」的路，
// 让老师看完题就能接着写答案，不用先记住题目在哪儿、再去别处找。
//
// 但面板里那块「题目信息」是 node("details", ...) 建出来的 <details>，
// 从来没设过 open —— 默认折叠。折叠的 <details> 里的内容在 Chrome 里是
// content-visibility:hidden，元素还占着位置（getBoundingClientRect 照常报
// 坐标），但人看不见也点不到。
//
// 实测 1440 / 1024 / 900 宽：
//   按钮 rect = [1284, 463, 1381, 491]（在 question-inspector-content 可视区
//   [1147, 142, 1397, 435] 底下）
//   Playwright is_visible() = false，真实点击超时失败
//   容器 scrollHeight == clientHeight（293 == 293）→ 连滚都滚不到它
//
// 这个测试盯住：只要 details 里有可点的入口，那块 <details> 就必须展开，
// 别让入口折起来；而没有入口的题保持折叠，右侧面板不摊开一屏没用信息。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

const details = js.match(/const info = node\("details", "question-inspector-info"\);[\s\S]{0,3000}?info\.append\(facts\);[\s\S]{0,600}?info\.open = true;/);
assert.ok(details, "找得到详情栏「题目信息」那块 details 的代码");
assert.match(
  details[0],
  /if \(facts\.querySelector\("\.question-inspector-fact-action"\)\) info\.open = true;/,
  "details 里有可点入口时必须自动展开：不展开的话，「为本题写答案」的入口折起来就够不着"
);
// 别图省事直接 info.open = true —— 那样所有题的右侧面板都会摊开
assert.doesNotMatch(
  js,
  /const info = node\("details", "question-inspector-info"\);\s*info\.open = true;/,
  "不该无条件展开：没有可点入口的题要保持折叠"
);

// 入口按钮还得留在 facts 里，别被挪走
assert.match(
  js,
  /inspectorFactAction\(facts, "答案解析", text, isAI \? "编辑 \/ 核对答案解析" : "为本题写答案"/,
  "「为本题写答案」这个入口必须留在详情栏的答案解析那一行里"
);
assert.match(
  js,
  /openAnswerEditor\(\[item\], \{ scope: "library" \}\)/,
  "入口点开的是答案编辑器，别指错地方"
);

console.log("详情栏里的「为本题写答案」入口够得着：OK");
