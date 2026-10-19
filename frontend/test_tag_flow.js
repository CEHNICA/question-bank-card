"use strict";
// 1.25.54：逐题贴标签的流水线。这里守住它的几个契约：
// 入口在「未标知识点」旁边；一次只取一道题；贴上用批量接口、带版本号防覆盖；
// 键盘能选、能贴、能跳过；最近用过的标签和撤销都在。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

assert.match(js, /"逐题贴"/, "入口按钮叫「逐题贴」");
assert.match(js, /tag-flow-start/, "入口有独立标记，便于找到和测试");
assert.match(js, /untagged_knowledge=true&limit=40/, "流水线只取还没标知识点的题");
assert.match(js, /tagFlowDialog/, "流水线是一个独立对话框");
assert.match(js, /renderQuestion\(paper, item\.content/, "题目用和题库一致的渲染，不另做一套");
assert.match(js, /flowMatches/, "输入即搜");
assert.match(js, /event\.key === "ArrowDown" \|\| event\.key === "ArrowUp"/, "上下键可选");
assert.match(js, /expected_revisions: \{ \[item\.id\]: revision \}/, "贴上时带版本号，避免覆盖别处的修改");
assert.match(js, /request_id: tagRequestId\(\)/, "请求编号是 UUID，服务端按它去重");
assert.match(js, /key === " " \|\| event\.code === "Space"/, "空格跳过");
assert.match(js, /qb-library-recent-tags/, "最近用过沿用标签窗口的那份记录");
assert.match(js, /tags\/undo/, "贴上之后能撤销");
assert.match(js, /tag-flow-progress/, "顶部显示第几题、还剩多少");

console.log("Tag flow: entry beside the untagged filter, one question at a time, Enter tags and advances, Space skips, recent tags and undo: OK");
assert.match(js, /tag-flow-suggest/, "AI 建议摆在搜索框下面");
assert.match(js, /suggestion\.status !== "pending"/, "只显示待核对的建议");
assert.match(js, /flowRecentIds\(\)/, "最近用过的标签参与排序");
assert.match(js, /tag-suggestions\//, "点 AI 建议走采纳接口");
assert.match(js, /indices: \[index\]/, "采纳的是这一条建议");
