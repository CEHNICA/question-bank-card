"use strict";

// 题库工作台首屏不该把主线程堵住 1.5 秒。
//
// 实测（1440 宽，Playwright + CDP Performance 计数器）：
//   首张卡片出现   1511 ms
//   LayoutCount    59 次 / 654 ms
//   RecalcStyle    75 次 / 211 ms
//   DOM 节点       34086
//
// 两个明确的浪费：
//
// 1. render() 把 40 张题卡**逐个** ui.list.append(node) 插进 live DOM ——
//    每插一张就触发一次样式重算，75 次 RecalcStyle 里的大头就是这么来的。
//    改成先攒进 DocumentFragment、最后一次性 append，样式重算从 N 次降到 1 次。
//
// 2. readingOverflowHints() 在 requestAnimationFrame 里跑，而且**边读边写**：
//    forEach 里读 clientWidth/scrollWidth（强制布局），读到溢出的就 after(hint)
//    写一个提示节点（写完又让布局失效），下一个字段再读一次 —— 读一次写一次
//    循环，把整页布局反复逼出来（Layout 59 次的大头）。
//    改成：先把所有溢出的字段收集起来（只读），再统一写（只写一次）。
//
// 另外，这个测量发生在 requestAnimationFrame 回调里 —— rAF 回调跑完才 paint，
// 于是这几百毫秒直接顶在首屏绘制前面。改成空闲时再做，题卡先出来。
//
// 这个测试盯住：
//   1. render() 必须用 DocumentFragment 批量插入；
//   2. readingOverflowHints 必须先收集再写（不能读写交替）；
//   3. 溢出提示的测量不许再挂在 requestAnimationFrame 上（顶在绘制前面）。

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

// ---- 1. 批量插入 ----
const render = bodyOf(js, "function render() {");
assert.match(
  render,
  /createDocumentFragment\(\)/,
  "render() 要先把卡片攒进 DocumentFragment，最后一次性插入：逐个 append 会触发 N 次样式重算"
);
assert.doesNotMatch(
  render,
  /ui\.list\.append\(node\)/,
  "render() 里不该再逐张 append 到 live DOM"
);

// ---- 2. 先读后写 ----
const overflow = bodyOf(js, "function readingOverflowHints(");
assert.match(
  overflow,
  /overflowFields/,
  "readingOverflowHints 要先把溢出的字段收集进 overflowFields"
);
const collectAt = overflow.indexOf("overflowFields");
const writeAt = overflow.search(/\.after\(|appendChild/);
assert.ok(collectAt >= 0 && writeAt >= 0, "读得到收集和写入两处代码");
assert.ok(
  collectAt < writeAt,
  "收集（读尺寸）必须排在写入（插提示节点）前面：读写交替会把布局反复逼出来"
);

// ---- 3. 不许再挂在 rAF 上 ----
assert.doesNotMatch(
  render,
  /requestAnimationFrame\(\s*\(\)\s*=>\s*readingOverflowHints\(/,
  "溢出提示的测量不许再挂在 requestAnimationFrame 上：rAF 回调跑完才 paint，等于把首屏挡在后面"
);
assert.match(
  js,
  /scheduleReadingOverflowHints\(/,
  "溢出提示改到空闲时再做（scheduleReadingOverflowHints）"
);

console.log("题库工作台首屏渲染不堵主线程：OK");
