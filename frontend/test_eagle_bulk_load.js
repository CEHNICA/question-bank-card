"use strict";

// 题库工作台（Eagle）首屏以前把整库拆成九次并发请求：
//
//     /api/library?limit=100&offset=0
//     /api/library?limit=100&offset=100
//     ...一共九次，810 道题
//
// 每次请求都把 facets / presence / tag_work 这些跟分页无关的统计重算一遍。
// 九次请求在同一个进程里排成一队，光 API 就十二秒 —— 老师打开工作台，
// 干等十几秒才看见东西。
//
// 第一步改成一次 bulk 请求（backend/core/library_browse.py 的 bulk=1）。
// 第二步发现还是不对：一次把 810 道题全拉回来，光这条接口两百多毫秒，
// 加上 JSON 解析和渲染，首张卡片要一秒多才出现 —— 而首屏只画 25 张。
//
// 现在改成「首批先画 + 后台补拉」：
//   首屏只取一批（LIBRARY_FIRST_BATCH 道），先把卡片画出来；
//   剩下的题目后台悄悄补拉，补进来的排在已画出来那批后面，屏幕不重画。
//
// 这个测试盯住四件事：
//   1. 首屏走 bulk，但只取一批，不是整库；
//   2. 旧的九次并发分页写法不许回来；
//   3. 后台补拉必须存在，而且必须带 sort=display（批次边界要对齐显示顺序）；
//   4. 别的首屏请求（资料、标签）不许被顺手改掉。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

// ---- 1. 首屏走 bulk，但只取一批 ----
assert.match(
  js,
  /\/api\/library\?bulk=1&limit=\$\{LIBRARY_FIRST_BATCH\}&offset=0&slim=1&sort=display/,
  "Eagle 首屏必须用 bulk=1 取首批，并且带 sort=display"
);
assert.match(
  js,
  /const LIBRARY_FIRST_BATCH = \d+;/,
  "必须定义首批条数 LIBRARY_FIRST_BATCH"
);
assert.doesNotMatch(
  js,
  /\/api\/library\?bulk=1&limit=1000&offset=0/,
  "首屏不许再一次性拉整库（810 道题两百多毫秒，首张卡片一秒多才出现）"
);

// ---- 2. 旧的九次并发分页不许回来 ----
assert.doesNotMatch(
  js,
  /remainingOffsets/,
  "不许再把整库拆成一批批并发拉（那正是十二秒的来源）"
);
assert.doesNotMatch(
  js,
  /\/api\/library\?limit=100&offset=\$\{/,
  "不许再有 limit=100 的偏移量拼接请求"
);
assert.doesNotMatch(
  js,
  /Promise\.all\(\s*remaining/,
  "不许再有剩余分批的 Promise.all 并发"
);

// ---- 3. 后台补拉必须存在 ----
assert.match(
  js,
  /async function loadRemainingLibraryItems\(/,
  "必须有后台补拉函数，首屏不能等整库"
);
assert.match(
  js,
  /bulk=1&limit=1000&offset=\$\{offset\}&slim=1&sort=display/,
  "后台补拉要走 bulk，一次一批，并且带 sort=display 让批次边界跟显示顺序对齐"
);
assert.match(
  js,
  /loadRemainingLibraryItems\(libraryLoadGeneration, totalCount, firstRawItems\.length\)/,
  "后台补拉要在首批渲染之后才启动"
);
// 首屏那段代码里不能有「先把整库拉完」的续拉循环
const firstScreen = js.match(/libFirstRes\.ok[\s\S]{0,4000}?loadRemainingLibraryItems\(/);
assert.ok(firstScreen, "应能找到首屏处理批次");
assert.doesNotMatch(
  firstScreen[0],
  /while\s*\(offset\s*<\s*totalCount\)/,
  "首屏路径里不许再有「拉完整库再渲染」的循环"
);

// ---- 4. 别的首屏请求不许被动 ----
assert.match(js, /\/api\/papers\?archived=1/, "资料列表照旧要拉");
assert.match(js, /\/api\/library\/tags/, "标签目录照旧要拉");

console.log("Eagle 首屏先画首批、后台再补拉：OK");
