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
// 现在改成一次 bulk 请求把整库拿完（backend/core/library_browse.py 的 bulk=1）：
// 题库特别大（超过一批）才续拉。卡片墙本来就是分块无限滚动，滚多少渲多少，
// 首屏根本不需要等全库。
//
// 这个测试盯住三件事：
//   1. 首屏走的是 bulk 一次拉全；
//   2. 旧的九次并发分页写法不许回来；
//   3. 别的首屏请求（资料、标签）不许被顺手改掉。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

// ---- 1. 首屏一次拉全 ----
assert.match(
  js,
  /\/api\/library\?bulk=1&limit=1000&offset=0/,
  "Eagle 首屏必须用 bulk=1 一次拉整库"
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

// ---- 3. 题库特别大时才续拉，而且一次一批 ----
assert.match(
  js,
  /while \(offset < totalCount\)/,
  "题库超过一批时要能续拉"
);
assert.match(
  js,
  /bulk=1&limit=1000&offset=\$\{offset\}/,
  "续拉也要走 bulk，一次一批"
);

// ---- 4. 别的首屏请求不许被动 ----
assert.match(js, /\/api\/papers\?archived=1/, "资料列表照旧要拉");
assert.match(js, /\/api\/library\/tags/, "标签目录照旧要拉");

console.log("Eagle 首屏整库一次拉走：OK");
