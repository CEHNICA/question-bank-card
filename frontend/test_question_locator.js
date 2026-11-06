"use strict";

// 1.26.43：全库只留一套定位说法。
//
// 同一本教材里题号会从小节重新开始，「第 1 题」在书上有上百处，
// 光报题号等于没报。所以定位句永远是三样东西：原卷页码 + 章节名 + 题号。
//
// 页码是唯一的硬锚点：章节名会重号（「练习」「思考」全书几十处），
// 题号也会重号，只有「书上第几页」认得出来。
// 所以页码不再只在「没有章节名时」才报 —— 有章节名也得报。
const assert = require("node:assert/strict");
const L = require("./question-locator.js");

// ── 短章节名：教材的「习题 6.1」认，试卷的一整段大题说明不认 ────
assert.equal(L.sectionLabel("习题 6.1"), "习题 6.1");
assert.equal(L.sectionLabel("均值不等式"), "均值不等式");
assert.equal(L.sectionLabel("一、选择题：本大题共8小题，每小题5分。"), "", "试卷的大题说明不是章节名");
assert.equal(L.sectionLabel("第1节 公式法 · 例题1"), "", "13 字，超出上限");
assert.equal(L.sectionLabel(""), "");
assert.equal(L.sectionLabel(null), "");

// ── 教材的小节名：编号 + 中文标题，12 字卡不住的也算 ────────────
// 1.26.44：真库里有 9 道题的章节名是「5.5.2 简单的三角恒等变换」这类，
// 以前按 12 字上限整段丢掉，卡面上只剩「第 X 页」，定位不到小节。
assert.equal(L.sectionLabel("5.5.2 简单的三角恒等变换"), "5.5.2 简单的三角恒等变换", "教材小节名要显示，别被 12 字上限截掉");
assert.equal(L.sectionLabel("4.5.3 函数模型的应用"), "4.5.3 函数模型的应用");
assert.equal(L.sectionLabel("2.1 等式性质与不等式性质"), "2.1 等式性质与不等式性质");
assert.equal(L.sectionLabel("3.1.2 函数的表示法"), "3.1.2 函数的表示法");
assert.equal(L.sectionLabel("20.1 三角函数的图像与性质"), "20.1 三角函数的图像与性质");
assert.equal(L.sectionLabel("1.178 097 245."), "", "数字不是章节名");
assert.equal(L.sectionLabel("1. 观察实际情景，发现和提出问题"), "", "一整句话不是章节名");
assert.equal(L.sectionLabel("1. 观察实际情景并提出问题"), "", "单级编号不是教材小节名，得多级点号编号");
assert.equal(L.sectionLabel("2.1 观察实际情景，发现并提出问题"), "", "小节名的标题里带逗号就不算");
assert.equal(L.sectionLabel("2.1 函数的基本性质。"), "", "标题带句号就不算");
assert.equal(L.sectionLabel("20.1 这是一个非常非常非常长的教材小节名称"), "", "太长也不显示");
assert.equal(L.sectionLabel("一、单选题（每题3分，共30分）"), "", "试卷的大题说明不是章节名");
assert.equal(L.sectionLabel("1.2\n函数的基本性质"), "", "断成两行的不是章节名");
assert.equal(
  L.positionOf({ number: 3, page: 12, section: "5.5.2 简单的三角恒等变换" }),
  "第 12 页 · 5.5.2 简单的三角恒等变换",
  "小节名上卡面时，页码照旧在前面"
);
assert.equal(
  L.locate({ number: 3, page: 12, section: "5.5.2 简单的三角恒等变换" }),
  "第 12 页 · 5.5.2 简单的三角恒等变换 · 第 3 题"
);

// ── 章节名：先认真章节名，其次短章节名，其余一律不算 ────────────
assert.equal(L.scopeOf({ group_title: "章节01 · 公式法" }), "章节01 · 公式法");
assert.equal(L.scopeOf({ group: { title: "第2章 函数" } }), "第2章 函数");
assert.equal(L.scopeOf({ group: "第3章 三角" }), "第3章 三角");
assert.equal(L.scopeOf({ group: { title: "第 3 组" } }), "", "「第 N 组」是位置标签，不是章节名");
assert.equal(L.scopeOf({ group: { title: "第 12 页 · 第 3 组" } }), "", "位置标签不是章节名");
assert.equal(L.scopeOf({ group: { title: "第 3 组" }, section: "习题 6.1" }), "习题 6.1");
assert.equal(L.scopeOf({ section: "习题 6.1" }), "习题 6.1");
assert.equal(L.sectionLabel("一、选择题：本大题共8小题"), "");
assert.equal(L.scopeOf({ section: "一、选择题：本大题共8小题" }), "");
assert.equal(L.scopeOf({ content: { section: "均值不等式" } }), "均值不等式", "题库条目的章节名在 content.section");
assert.equal(
  L.scopeOf({ content: { source_group: { title: "章节02 · 配方法" } } }),
  "章节02 · 配方法",
  "题库条目的题组名在 content.source_group"
);
assert.equal(L.scopeOf({ scope: "后端算好的章节名" }), "后端算好的章节名", "后端算好了就以它为准");
assert.equal(L.scopeOf({}), "");
assert.equal(L.scopeOf(null), "");

// ── 题号：教材里的例题就叫「例 1」 ────────────────────────────
assert.equal(L.numberText({ number: 3 }), "第 3 题");
assert.equal(L.numberText({ number: 3, source_kind: "example" }), "例 3");
assert.equal(L.numberText({}), "");
assert.equal(L.numberText({ number: null }), "");

// ── 页码：书上第一次出现的那一页 ──────────────────────────────
assert.equal(L.pageOf({ number: 1, page: 128 }), 128);
assert.equal(L.pageOf({ regions: [{ page_idx: 4 }, { page_idx: 0 }] }), 1, "取最小的那页");
assert.equal(L.pageOf({ content: { sources: [{ page_idx: 6 }] } }), 7);
assert.equal(L.pageOf({ number: 1 }), null);
assert.equal(L.pageOf({ number: 1, page: 0 }), null, "第 0 页不是页码");

// ── 全部页码：去重、从小到大 ──────────────────────────────────
assert.deepEqual(L.pagesOf({ regions: [{ page_idx: 0 }, { page_idx: 4 }, { page_idx: 0 }] }), [1, 5]);
assert.deepEqual(L.pagesOf({}), []);
assert.deepEqual(L.pagesOf({ regions: [{ page_idx: "x" }] }), []);
assert.deepEqual(L.pagesOf({ regions: [{ page_idx: 2 }, { page_idx: 1 }] }), [2, 3], "页码从小到大");

// ── 定位句：页码 · 章节名 · 题号 ───────────────────────────────
assert.equal(L.locate({ number: 1, page: 128 }), "第 128 页 · 第 1 题");
assert.equal(
  L.locate({ group_title: "章节01 · 公式法", number: 1, page: 128 }),
  "第 128 页 · 章节01 · 公式法 · 第 1 题",
  "有章节名时页码照样要报 —— 章节名也会重号"
);
assert.equal(L.locate({ number: 1 }), "第 1 题", "没有原卷位置就没有页码");
assert.equal(L.locate({ number: 1, page: 0 }), "第 1 题");
assert.equal(L.locate({ section: "均值不等式", number: 2, page: 151 }), "第 151 页 · 均值不等式 · 第 2 题");
assert.equal(L.locate({ number: null, section: "做一做", page: 42 }), "第 42 页 · 做一做");
assert.equal(L.locate({}), "栏目题");

// ── 身份与位置：卡上分两格，各说各的，同一句话不说两遍 ──────────
assert.equal(L.identityOf({ number: 3 }), "第 3 题");
assert.equal(L.identityOf({ number: null, section: "做一做" }), "做一做");
assert.equal(L.identityOf({}), "栏目题");
assert.equal(L.positionOf({ number: 3, page: 12 }), "第 12 页");
assert.equal(L.positionOf({ number: 3, page: 12, section: "习题 6.1" }), "第 12 页 · 习题 6.1");
assert.equal(
  L.positionOf({ number: null, section: "做一做", page: 42 }),
  "第 42 页",
  "没有题号时章节名已经是身份，不再说一遍"
);
assert.equal(L.positionOf({ number: null, section: "做一做" }), "");
assert.equal(L.positionOf({ number: 3 }), "");

// ── 题干开头：认题时扫一眼就能对上 ────────────────────────────
assert.equal(L.snippet("  已知\n$x=1$  ", 60), "已知 $x=1$");
assert.equal(L.snippet("一二三四五六七八九十", 5), "一二三四五…");
assert.equal(L.snippet("", 60), "");
assert.equal(L.snippet(null, 60), "");

console.log("question locator: ok");
