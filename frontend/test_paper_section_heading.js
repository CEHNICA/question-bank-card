"use strict";
// 组卷上每个大题前面那一行：屏幕预览和导出的文件必须是同一句话。
//
// 老师 2026-10-24 早上 7 点报的问题：「试题篮中的和导出的pdf不一致是怎么回事」。
// 查下来：屏幕上的组卷预览印的是「二、填空题」，导出的 PDF 印的是原卷那一整行
// 「二、填空题：本题共 4 小题，每小题 5 分，共 20 分」—— 而那一行里的题数、分值、
// 题号范围说的全是**原卷**，跟老师自己挑的这几道题对不上。
//
// 这一版量的不是源码里有没有那两个字，是**真算出来的标题**：
//   1. 屏幕那一侧：frontend/exam-export.js 的 sectionHeading（预览就是用它排的）；
//   2. 导出那一侧：backend/core/library_export.py 的 _section_heading（PDF、Word 都用它）。
// 两边读同一份 frontend/testdata/paper_section_headings.json —— 改一边就红。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const examExport = require("./exam-export.js");
const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const fixture = JSON.parse(
  fs.readFileSync(path.join(__dirname, "testdata", "paper_section_headings.json"), "utf8"));

assert.ok(fixture.cases.length >= 8, "取值至少要覆盖试卷大题说明、教材短章节名和混合的那几种");
assert.equal(typeof examExport.sectionHeading, "function", "ExamExport 负责算大题标题");
assert.equal(typeof examExport.shortSectionLabel, "function", "短章节名的判定只有一处");

// ---- 一、屏幕那一侧：每一组算出来的标题都对 --------------------------------
for (const { name, sections, index, group_name: groupName, expected } of fixture.cases) {
  const group = sections.map((section) => ({ content: { section } }));
  assert.equal(examExport.sectionHeading(group, index, groupName), expected, name);
}

// ---- 二、原卷那一整行再也不会印出来 ----------------------------------------
for (const { sections, index, group_name: groupName } of fixture.cases) {
  const group = sections.map((section) => ({ content: { section } }));
  const heading = examExport.sectionHeading(group, index, groupName);
  for (const section of sections) {
    if (!section || section.length <= 12) continue;
    assert.ok(!heading.includes(section), `大题标题里不该出现原卷那一整行：${heading}`);
    assert.ok(!/本题共|共\s*\d+\s*分|第\s*\d+\s*题/.test(heading),
      `大题标题里不该出现原卷的题数、分值、题号范围：${heading}`);
  }
}

// ---- 三、预览真的用的是这一个函数 ------------------------------------------
assert.match(libraryJs, /node\("h3", "print-section", window\.ExamExport\.sectionHeading\(group, index, name\)\)/,
  "预览的大题标题必须走 ExamExport.sectionHeading —— 屏幕和导出必须是同一个定义");

// ---- 四、短章节名的判定只有一处（卡面和组卷标题共用） ----------------------
assert.match(libraryJs, /function shortSection\(item\) \{\s*return window\.ExamExport\.shortSectionLabel\(item\?\.content\?\.section\);/,
  "卡面上的章节名判定也走 ExamExport.shortSectionLabel，别再各写一遍");
assert.match(examExport.sectionHeading.toString(), /shortSectionLabel\(/,
  "组卷标题里的章节名判定必须用共享的那一条");
assert.doesNotMatch(examExport.sectionHeading.toString(), /SECTION_LABEL_MAX|SECTION_LABEL_STOP/,
  "组卷标题不能自己再抄一份「多长算短、哪些符号算噪音」的规矩");

// ---- 五、题型名的序号：第几组就写「一、二、三…」 --------------------------
assert.equal(examExport.sectionHeading([{ content: {} }], 0, "选择题"), "一、选择题");
assert.equal(examExport.sectionHeading([{ content: {} }], 1, "填空题"), "二、填空题");
assert.equal(examExport.sectionHeading([{ content: {} }], 4, "其他"), "五、其他");
assert.equal(examExport.sectionHeading([{ content: {} }], 5, "其他"), "六、其他");

console.log("The group heading on screen and in the exported file: the same words: OK");
