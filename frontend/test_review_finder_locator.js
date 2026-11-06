"use strict";

// 找题结果是清单型界面：每一行都得能对回原书。
//
// 1.26.43：标题里已经写了「书上第几页 · 章节名 · 第 N 题」，
// 右边那行就只补它之外的页 —— 同一个页码不说两遍。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

// 标题用统一说法：页码 + 章节名 + 题号。
assert.match(js, /window\.QBQuestionLocator\?\.locate\(q\)/, "找题结果用统一说法");

// 右边那行只补标题之外的页。
assert.match(js, /const firstPage = window\.QBQuestionLocator\?\.pageOf\(q\);/, "先问清楚书上第几页");
assert.match(
  js,
  /const otherPages = pages\.filter\(\(page\) => page !== firstPage\);/,
  "只补标题之外的页，同一个页码不说两遍"
);
assert.match(
  js,
  /const meta = otherPages\.length \? `另在第 \$\{otherPages\.join\("、"\)\} 页` : pages\.length \? "" : "原图题";/,
  "只有一页时右边那行不出现"
);
assert.match(
  js,
  /if \(meta\) row\.append\(el\("span", "review-finder-result-meta", meta\)\);/,
  "没有要补的就不留一个空行"
);

console.log("review finder locator: ok");
