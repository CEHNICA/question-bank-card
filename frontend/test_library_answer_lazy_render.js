"use strict";

// 折叠着的「看答案」不该在首屏就把答案解析排版出来。
//
// 实测（1440 宽，/library 首屏 40 张卡）：
//   .katex 总数        1085
//   其中在 .qb-analysis  556   ← 答案解析
//   其中在 .qb-stem      423
//   其中在 .qb-option     47
//   DOM 节点            34086
//
// 答案解析是 answerReveal() 拼进每张卡片的 <details class="library-answer">。
// 那个 details 绝大多数时候是**折着的** —— 内容早就在 DOM 里（只是看不见），
// 于是每张卡白白排版三四十个公式。40 张卡 = 556 个 KaTeX 块、两万多个节点，
// 全花在看不见的地方：首屏第一张卡片要等 1.5 秒。
//
// 改法：折叠时不排版，第一次展开时才把答案、解析、AI 初稿、历史排进去。
// 已经展开着的题（state.opened 里有）照旧立刻有内容。
//
// 这个测试盯住：
//   1. 答案内容的构建必须在 fill() 里，不能无条件在顶层跑；
//   2. 已经展开的题要立刻有内容（不能等用户再点一下）；
//   3. 折叠的题在展开那一刻要补上内容。

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

const reveal = bodyOf(js, "function answerReveal(");

// ---- 1 & 2. 内容构建要挂在 fill 里，且已展开时立刻执行 ----
assert.match(reveal, /const fill = /, "答案内容要放进一个 fill() 里");
const fillAt = reveal.indexOf("const fill = ");
const rowsAt = reveal.indexOf("QB.answerRows");
assert.ok(rowsAt >= 0, "answerReveal 里还排版答案");
assert.ok(
  rowsAt > fillAt,
  "答案内容必须在 fill() 里构建：顶层无条件构建等于折叠着也把公式排完"
);
assert.match(
  reveal,
  /if\s*\(\s*box\.open\s*\)\s*fill\(\)/,
  "已经展开的题（state.opened 里有）要立刻有内容，不能等用户再点一下"
);

// ---- 3. 折叠的那一张，展开那一刻要补上内容 ----
assert.match(
  reveal,
  /box\.addEventListener\("toggle"[\s\S]{0,200}?box\.open[\s\S]{0,120}?fill\(\)/,
  "折叠的题在展开那一刻（toggle）要把内容补上"
);

console.log("折叠的「看答案」不在首屏排版：OK");
