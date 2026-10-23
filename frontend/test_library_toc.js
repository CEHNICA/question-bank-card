"use strict";
// 1.26.6：照着课本目录写教材目录。
// 群里那位同学的原话：「目录能不能自己根据教材目录来编写」—— 这里验的是
// 把课本目录的**文字**解析成一棵树这件事：认得常见的几种写法、分得清层级、
// 不把页码行当目录。
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const toc = require(path.join(__dirname, "library-toc.js"));

const names = tree => toc.flatten(tree.roots).map(entry => `${"  ".repeat(entry.depth)}${entry.name}`);

const textbook = [
  "第一章 集合",
  "1.1 集合的含义",
  "1.2 外集",
  "第二章 一元二次函数",
  "2.1 二次函数的图象",
  "  2.1.1 开口方向",
].join("\n");

const parsed = toc.parse(textbook);
assert.equal(parsed.count, 6, "每一行都认出来了");
assert.deepEqual(names(parsed), [
  "第一章 集合",
  "  1.1 集合的含义",
  "  1.2 外集",
  "第二章 一元二次函数",
  "  2.1 二次函数的图象",
  "    2.1.1 开口方向",
], "章 → 节 → 小节 分得清楚");

// 中文编号和括号编号
const chinese = toc.parse([
  "第一章 集合",
  "一、集合的概念",
  "（一）集合的含义",
  "（二）外集",
].join("\n"));
assert.deepEqual(names(chinese), [
  "第一章 集合",
  "  一、集合的概念",
  "    （一）集合的含义",
  "    （二）外集",
], "「一、」「（一）」按深一级排");

// 页码行、页眉页脚不该进目录
const noisy = toc.parse([
  "第一章 集合 ...................... 1",
  "1.1 集合的含义 ................... 3",
  "12",
  "——— 3 ———",
].join("\n"));
assert.equal(noisy.count, 2, "只有目录行算数");

// 空目录不炸
const empty = toc.parse("   \n\n  \n");
assert.equal(empty.count, 0);
assert.deepEqual(empty.roots, []);

console.log("Textbook table of contents: parsing the pasted text into a tree: OK");
