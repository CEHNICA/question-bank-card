"use strict";

// Eagle 顶栏在窄屏下会把「筛选」按钮挤出、压到右组身上 —— 按钮看得见，点不到。
//
// 实测（Playwright，viewport 宽 = 视口宽）：
//   1440 / 1024 / 900 / 820  → 正常
//   768 / 720 / 640          → #btnToggleFilterPanel 中心点 elementFromPoint
//                              命中的是「篮 0」按钮，真实 click() 超时失败
//
// 根因：顶栏那一行是 justify-between 的两组。
//   左组 = 搜索框 + 「筛选」按钮，flex-1 min-w-0
//   右组 = 篮 / 共 N 题 / 全选 / 存视图，shrink-0 whitespace-nowrap，固定 288px
// 窄屏时左组被右组挤到 144px，而搜索框容器写死了 min-w-[10rem]（160px）、
// 左组又是 overflow:visible —— 合计需要的 236px 装不下，搜索框和「筛选」按钮
// 直接溢出到右组上，把「篮」按钮压在下面。
//
// 修法：
//   1) 搜索框容器去掉 min-w-[10rem] 硬下限（min-w-0），窄屏时能被压窄；
//   2) 右组在窄屏瘦身：「存视图」只留图标（<1024）；顶栏不再显示「共 N 题」；
//      「全选」旁不再常驻「已勾选 N 题」，改悬停提示。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

// 1) 搜索框容器：不能再有 min-w-[10rem] 硬下限
const inputAt = html.indexOf('id="globalSearchInput"');
assert.ok(inputAt > 0, "找得到顶栏搜索框");
const searchWrap = html.slice(Math.max(0, inputAt - 600), inputAt);
assert.match(searchWrap, /min-w-0/, "搜索框容器必须是 min-w-0：写死 min-w-[10rem] 会撑破左组");
assert.doesNotMatch(
  searchWrap,
  /min-w-\[10rem\]/,
  "搜索框容器不能再有 min-w-[10rem] 硬下限"
);

// 2) 顶栏不再显示「共 N 题」（左侧文件夹已有数字）；命中数只留隐藏节点给内部更新
assert.match(html, /id="matchedQuestionCount"/);
assert.doesNotMatch(html, /id="matchedCountJumpWrap"/);
assert.doesNotMatch(html, /id="btnMatchedCountJump"/);
assert.doesNotMatch(html, />\s*共\s*<strong id="matchedQuestionCount"/);

// 3) 「全选」只留二字；勾选数改悬停提示，不常驻括号文案
assert.match(html, /id="selectAllLabel"/);
assert.match(html, /data-tip-title="已勾选 0 题"/);
assert.match(html, /<span>全选<\/span>/);
assert.doesNotMatch(html, /id="selectedCountText"/);
assert.match(html, /function syncSelectAllCountTip\(/);

// 4) 存视图窄屏只留图标
assert.match(
  html,
  /<span class="hidden lg:inline">存视图<\/span>/,
  "「存视图」在 <1024 只留图标（title 里有完整说明）"
);

console.log("Eagle 顶栏窄屏不再压住「筛选」按钮：OK");
