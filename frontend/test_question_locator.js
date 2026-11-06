"use strict";

// 1.26.41：指出一道题的说法，只在这一处写一次。
//
// 同一本教材里题号会从小节重新开始，「第 1 题」在书上有上百处，
// 光报题号等于没报。这里钉住：章节名、题号、页码、题干开头各自怎么取，
// 以及没有章节名时用页码顶上。
const assert = require("node:assert/strict");
const L = require("./question-locator.js");

// ── 章节名：三种来法都要认 ─────────────────────────────────────
assert.equal(L.scopeOf({ group_title: "章节01 · 公式法" }), "章节01 · 公式法");
assert.equal(L.scopeOf({ group: { title: "章节02 · 配方法" } }), "章节02 · 配方法");
assert.equal(L.scopeOf({ group: "章节03 · 换元法" }), "章节03 · 换元法");
assert.equal(L.scopeOf({ section: "第1节 公式法 · 例题1" }), "第1节 公式法 · 例题1");
assert.equal(L.scopeOf({ group: null, section: "均值不等式" }), "均值不等式");
assert.equal(L.scopeOf({}), "");
assert.equal(L.scopeOf(null), "");
assert.equal(L.scopeOf({ group_title: "   " }), "", "空白的章节名当没有");

// ── 题号：教材里的例题就叫「例 1」 ────────────────────────────
assert.equal(L.numberText({ number: 3 }), "第 3 题");
assert.equal(L.numberText({ number: 3, source_kind: "example" }), "例 3");
assert.equal(L.numberText({}), "这道题");
assert.equal(L.numberText({ number: null }), "这道题");

// ── 主定位：章节/小节 + 题号 ───────────────────────────────────
assert.equal(L.locate({ group_title: "章节01 · 公式法", number: 1 }), "章节01 · 公式法 · 第 1 题");
assert.equal(L.locate({ section: "第1节 公式法 · 例题1", number: 1 }), "第1节 公式法 · 例题1 · 第 1 题");
assert.equal(L.locate({ number: 1 }), "第 1 题");
assert.equal(L.locate({ group_title: "章节01 · 公式法", number: 2, source_kind: "example" }), "章节01 · 公式法 · 例 2");

// ── 没有章节名时，题号在书里重号，只能靠原卷页码认 ───────────
assert.equal(L.locateWithPage({ number: 1, page: 128 }), "第 128 页 · 第 1 题");
assert.equal(
  L.locateWithPage({ group_title: "章节01 · 公式法", number: 1, page: 128 }),
  "章节01 · 公式法 · 第 1 题",
  "已经有章节名了就不用页码顶"
);
assert.equal(L.locateWithPage({ number: 1 }), "第 1 题");
assert.equal(L.locateWithPage({ number: 1, page: 0 }), "第 1 题", "第 0 页不是页码");

// ── 页码：去重、从小到大 ───────────────────────────────────────
assert.deepEqual(L.pagesOf({ regions: [{ page_idx: 0 }, { page_idx: 4 }, { page_idx: 0 }] }), [1, 5]);
assert.deepEqual(L.pagesOf({}), []);
assert.deepEqual(L.pagesOf({ regions: [{ page_idx: "x" }] }), []);
assert.deepEqual(L.pagesOf({ regions: [{ page_idx: 2 }, { page_idx: 1 }] }), [2, 3], "页码从小到大");

// ── 题干开头：认题时扫一眼就能对上 ────────────────────────────
assert.equal(L.snippet("  已知\n$x=1$  ", 60), "已知 $x=1$");
assert.equal(L.snippet("一二三四五六七八九十", 5), "一二三四五…");
assert.equal(L.snippet("", 60), "");
assert.equal(L.snippet(null, 60), "");

console.log("question locator: ok");
