"use strict";

// 题库工作台（Eagle）侧栏要的资料条目只有四样：id、名称、总题数、已入库数。
//
// /api/papers 的默认做法是为每份资料跑一遍 card_verdicts：把该资料所有题卡的
// JSON 字段拉出来、算指纹、逐张判状态，再把 pages / structure /
// processing_plan 这些胖 JSON 原样传出去。13 份资料里有一份 509 题的教材，
// 这一项就吃掉首屏将近 0.4 秒，还白传几百 KB。
//
// 现在侧栏改走 slim=1：口径（total / published）与完整模式逐份相等，但不算
// 逐卡判定、不带胖字段。录入页和审核页仍然走完整模式，它们真的要用这些字段。
//
// 这个测试盯住：
//   1. Eagle 的资料请求必须带 slim=1；
//   2. 录入页/审核页的完整资料请求不许被改成 slim（它们要结构数据）；
//   3. Eagle 侧栏只读 counts.total / counts.published，别顺手加新依赖。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const eagle = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

// ---- 1. Eagle 的资料请求走 slim ----
const eaglePapersCalls = [...eagle.matchAll(/\/api\/papers\?archived=1([^`'"]*)/g)].map((m) => m[0]);
assert.ok(eaglePapersCalls.length >= 1, "Eagle 至少要请求一次资料列表");
for (const call of eaglePapersCalls) {
  assert.match(
    call,
    /slim=1/,
    `Eagle 的资料请求必须带 slim=1（否则白算一遍逐卡判定）：${call}`
  );
}

// ---- 2. 完整模式的资料请求还在 ----
assert.match(
  app,
  /await api\("\/api\/papers"\)/,
  "录入页的资料列表仍走完整模式，它要结构数据"
);
assert.doesNotMatch(
  app,
  /await api\("\/api\/papers\?slim=1"\)/,
  "录入页不许换成 slim：那页要用 structure / pages"
);

// ---- 3. 侧栏只读这两个计数字段 ----
const countFields = new Set([...eagle.matchAll(/counts\.(\w+)/g)].map((m) => m[1]));
for (const field of countFields) {
  assert.ok(
    ["total", "published"].includes(field),
    `Eagle 侧栏只该用 counts.total / counts.published，多出来的 ${field} 说明 slim 少给了字段`
  );
}

console.log("Eagle 资料清单走 slim：OK");
