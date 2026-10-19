"use strict";
// 1.25.58：教材目录长在题库里。守住几个契约：开关在筛选栏；左栏是章 → 节的树，
// 每个节点带题数；点一章按 tag_any 过滤（含子标签）；「全部题目」「未标知识点」
// 是一等入口；开合状态记在本机。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");

assert.match(html, /id="libraryDirectoryToggle"/, "筛选栏里有目录开关");
assert.match(js, /id = "libraryDirectory"/, "目录是题库里的左栏，不是另一个页面");
assert.match(js, /library-directory-open/, "开关切换一个页面状态");
assert.match(js, /tagAny = String\(tag\.id\)/, "点一章按标签筛选");
assert.match(js, /tagMode = "any"/, "筛选包含子标签");
assert.match(js, /directoryCount\(tag\)/, "每个节点写这一层有几道题");
assert.match(js, /个没用到的/, "没题的子标签先收起来");
assert.match(js, /全部题目/, "回到全部题目的入口");
assert.match(js, /未标知识点 · \$\{missing\} 题/, "未标知识点也是目录里的一等入口");
assert.match(js, /qb-library-directory/, "开合状态记在本机");
assert.match(css, /grid-template-columns: 236px minmax\(0, 1fr\) 288px/, "桌面上目录占左栏，题目居中");
assert.match(css, /max-width: 1099px/, "窄屏上目录改成左滑层");

console.log("Library directory: toggle, chapter tree with counts, tag_any filtering, all/untagged entries, remembered state: OK");
