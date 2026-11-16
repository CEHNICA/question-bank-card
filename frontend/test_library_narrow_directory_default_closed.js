"use strict";

// 窄屏上目录面板不能一进来就盖住正文。
//
// ≤1099px 时 .library-directory 变成 position:fixed、z-index:57 的左侧浮层，
// 宽 300px，从顶栏底下一直盖到底；而卡片列表 .library-list 在窄屏下从
// x=16 开始 —— 也就是说浮层整块压在卡片上。
//
// 实测（900 / 768 宽，进页面默认状态）：
//   目录浮层 rect = [0, 250, 300, 800]
//   第一张卡的复选框 rect = [64, 305, 81, 322]
//   elementFromPoint(复选框中心) 落在「＋ 按课本目录新建」按钮上
//   → 老师点第一张卡的勾选框，勾不上，还可能误触「按课本目录新建」。
//
// 宽屏（≥1100px）侧栏在栅格列里（grid-column:1），不覆盖正文，照旧自动打开。
//
// 这个测试盯住：
//   1. 窄屏初始化时不得自动打开目录；
//   2. 宽屏的自动打开别被顺手改掉；
//   3. 窄屏得留着「目录」这个入口，不能把开关弄没了；
//   4. 窄屏浮层那条 CSS 规则别被删（它是「打开后是抽屉」的依据）。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");

// ---- 1 & 2. 初始化那段必须先判窄屏 ----
const init = js.match(/try\s*\{[\s\S]{0,600}?localStorage\.getItem\("qb-library-directory"\)[\s\S]{0,300}?directoryToggle\(true\)/);
assert.ok(init, "找得到「记住目录开关状态并自动打开」这段初始化代码");
const block = init[0];
assert.match(
  block,
  /matchMedia\("\(max-width: 1099px\)"\)/,
  "自动打开目录之前必须先判断窄屏：窄屏的目录是盖在正文上的浮层"
);
assert.match(
  block,
  /!narrow|!isNarrow|narrow\s*\?|isNarrow\s*\?/,
  "窄屏时不能走进自动打开目录那条分支"
);
assert.match(
  block,
  /directoryToggle\(true\)/,
  "宽屏仍然要自动打开目录（侧栏在栅格列里，不挡正文）"
);

// ---- 3. 窄屏得有入口 ----
assert.match(html, /id="libraryDirectoryToggle"/, "顶栏得留着「目录」开关，窄屏才进得去");
assert.match(js, /libraryDirectoryToggle"\)\?\.addEventListener\("click"/, "「目录」开关的点击监听得挂着");

// ---- 4. 窄屏浮层规则还在 ----
assert.match(
  css,
  /@media[^{]*max-width:\s*1099px[^{]*\{[\s\S]{0,2000}?\.library-directory\s*\{[\s\S]{0,400}?position:\s*fixed/,
  "窄屏的目录浮层规则（position:fixed）得留着：打开时它是抽屉"
);
assert.match(css, /--library-directory-top/, "浮层上沿别写死顶栏高度（上一轮修过，别退回去）");

console.log("窄屏上目录面板不默认盖住正文：OK");
