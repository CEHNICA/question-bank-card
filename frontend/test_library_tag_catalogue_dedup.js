"use strict";

// /library 首屏不该把同一份标签目录拉两遍。
//
// 实测（1440 宽，Playwright 拦 fetch + 调用栈）：
//   [50 ms] GET /api/library/tags
//        at tagRequest      library.js:1776
//        at directoryLoad   library.js:6785   ← 初始化 directoryToggle(true) 带出来的
//        at directoryToggle library.js:6772
//   [51 ms] GET /api/library/tags
//        at tagRequest      library.js:1776
//        at loadTagCatalogue library.js:1785  ← 初始化又直接拉一遍
//
// 两个请求打的是同一个端点、同一份数据、同一时刻发出，纯属白等
// （这台的 /api/library/tags 单次约 394ms，还挤占了首屏的连接数）。
//
// 修法：目录面板和全局缓存共用同一份加载；并发调用时只发一个请求
//（单飞），失败也不留悬空的 pending。
//
// 这个测试盯住：
//   1. directoryLoad 不得自己 fetch 目录，必须走 loadTagCatalogue；
//   2. loadTagCatalogue 必须有并发单飞（pending promise），
//      否则初始化那两处仍然各发一次；
//   3. 单飞失败后必须把 pending 清掉，不能让后面所有调用都拿到同一个
//      失败结果（目录就再也刷不出来了）。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

function bodyOf(source, signature) {
  const start = source.indexOf(signature);
  assert.ok(start >= 0, `找得到 ${signature}`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    const char = source[index];
    if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open, index + 1);
    }
  }
  throw new Error(`${signature} 的函数体没闭合`);
}

// ---- 1. 目录面板不再自己发请求 ----
const directoryLoad = bodyOf(js, "async function directoryLoad(");
assert.doesNotMatch(
  directoryLoad,
  /tagRequest\(\s*"\/api\/library\/tags"/,
  "directoryLoad 不得自己拉目录：它和全局缓存拉的是同一份数据，两处并发就发两遍"
);
assert.match(
  directoryLoad,
  /loadTagCatalogue\(/,
  "directoryLoad 要复用 loadTagCatalogue 的结果"
);

// ---- 2. loadTagCatalogue 必须单飞 ----
const loadCatalogue = bodyOf(js, "async function loadTagCatalogue(");
assert.match(
  loadCatalogue,
  /tagCataloguePending/,
  "loadTagCatalogue 要有一个 pending promise：初始化那两处是并发调用，必须只发一个请求"
);
assert.match(
  loadCatalogue,
  /if\s*\(\s*tagCataloguePending\s*\)\s*return\s+tagCataloguePending/,
  "已有请求在飞时要复用它，而不是再发一次"
);
assert.equal(
  (loadCatalogue.match(/\/api\/library\/tags/g) || []).length,
  1,
  "loadTagCatalogue 里只能有一处真正的目录请求"
);

// ---- 3. 失败后不留悬空 pending ----
assert.match(
  loadCatalogue,
  /catch[\s\S]{0,400}?tagCataloguePending\s*=\s*null/,
  "请求失败后必须把 pending 清掉：否则目录面板会永远卡在同一个失败结果上"
);

console.log("标签目录只拉一次：OK");
