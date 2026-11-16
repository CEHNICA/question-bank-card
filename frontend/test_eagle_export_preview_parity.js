"use strict";

// Eagle 的「试题篮 → A4 预览」和「导出 PDF」是两套排版：
// 预览在浏览器里排（frontend/exam-export.js + exam-layout.js），
// 导出时后台再排一遍（backend/core/library_pdf.py）。
//
// 后台本来有一道防线：前端把预览时看到的页数一起送过去，
// 后台排出来对不上就拒发（409「排版有变化，请重新预览后导出」），
// 免得老师印出来的卷子跟他屏幕上看过的那份不是一回事。
//
// 题库页一直带着这个页数，Eagle 页忘了带 —— 防线在 Eagle 上是空的。
// 这个测试盯住 Eagle 也把它带上，而且别把老师误伤：
//   1. 预览排出来的页数得留下来；
//   2. 导出 PDF 时得把它送回去；
//   3. 没预览过、或者篮里换了题，就别送（否则白挡老师一次）；
//   4. 预览关着的时候改了排版设置，旧页数得作废。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");
const pdf = fs.readFileSync(path.join(__dirname, "..", "backend", "core", "library_pdf.py"), "utf8");
const library = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

// ---- 1. 预览的页数要留下来 ----
assert.match(
  js,
  /const previewState = \{ ready: false, items: \[\], pageCount: 0 \};/,
  "Eagle 的预览状态里得留着排出来的页数"
);
assert.match(
  js,
  /previewState\.pageCount = Number\(data\.page_count\) \|\| 0;/,
  "预览回报页数时得把它记下来"
);

// ---- 2. 导出 PDF 时得送回去 ----
assert.match(
  js,
  /preview_page_count: previewState\.pageCount/,
  "Eagle 导出 PDF 必须把预览页数带回后台，后台才能核对是不是同一份卷"
);

// ---- 3. 只在真预览过、篮没换的时候才送 ----
assert.match(
  js,
  /format === "pdf" && previewState\.pageCount > 0 && previewMatches/,
  "没预览过或篮里换了题就别送页数，不然会把老师白白挡一次"
);
assert.match(
  js,
  /const previewMatches = previewIds\.length === currentIds\.length/,
  "得先核对篮里的题跟预览时是不是同一批"
);

// ---- 4. 预览关着的时候改了设置，旧页数作废 ----
assert.match(
  js,
  /previewState\.pageCount = 0; return;/,
  "预览没开着就改了排版设置，屏幕上的那份卷已经作废，页数也得作废"
);

// ---- 5. 后台那道拒发还在，题库页那条也没被弄掉 ----
assert.match(pdf, /previewed != pages/, "后台必须继续核对页数，不对就拒发");
assert.match(library, /preview_page_count: Number\(ui\.paper\.dataset\.pageCount\)/, "题库页也得继续带页数");
assert.doesNotMatch(
  js,
  /ExamExport\.download\([\s\S]{0,400}?preview_page_count:\s*Number\(ui\./,
  "Eagle 页别照抄题库页那套取法"
);

console.log("Eagle 导出 PDF 带着预览页数回去核对：OK");
