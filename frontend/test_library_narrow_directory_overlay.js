"use strict";

// 窄屏上目录面板不能压住筛选行。
//
// ≤1099px 时 .library-directory 变成 position:fixed 的左侧浮层，从顶栏底下
// 一直盖到底。筛选行（.library-filterbar）和批量条（.library-bulk）正好贴在
// 它底下那一行，浮层一盖，「标签」「来源」两个胶囊就点不动了 —— 看着还在，
// 按下去没反应。实测 1024 宽时 elementFromPoint 落在目录面板上而不是胶囊上；
// 1100 和 1440 宽都正常，因为那时侧栏在栅格列里，不覆盖筛选行。
//
// 修法：浮层改成从「顶部控制区」的底下开始。控制区占多高由 JS 量出来写进
// --library-directory-top。
//
// 这个测试盯住：
//   1. 窄屏那条规则的 top 必须用 --library-directory-top，不能写死顶栏高度；
//   2. JS 必须真的量了筛选行和批量条，并写进那个变量；
//   3. 宽屏那条规则别被顺手改掉（宽屏侧栏在栅格列里，不该有浮层 top）。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

// ---- 1. 窄屏规则用变量 ----
const narrow = css.match(/@media \(max-width: 1099px\) \{[\s\S]*?\n\}/);
assert.ok(narrow, "library.css 里要有 ≤1099px 的窄屏规则");
const narrowBlock = narrow[0];
assert.match(
  narrowBlock,
  /body\.library-directory-open \.library-directory \{[\s\S]*?top:\s*var\(--library-directory-top/,
  "窄屏的目录浮层必须从 --library-directory-top 开始，不能从顶栏高度开始"
);
assert.doesNotMatch(
  narrowBlock,
  /top:\s*var\(--topbar-h\)\s*;/,
  "窄屏的目录浮层不能写死 topbar-h：那会把筛选行压在底下"
);

// ---- 2. JS 真的量了、写进了 ----
assert.match(
  js,
  /setProperty\(\s*["']--library-directory-top["']/,
  "JS 必须把量出来的控制区高度写进 --library-directory-top"
);
for (const selector of [".library-filterbar", ".library-bulk"]) {
  assert.ok(
    js.includes(`"${selector}"`),
    `量高度时必须把 ${selector} 算进去（少算一个就还会压住它）`
  );
}
assert.match(
  js,
  /addEventListener\(\s*["']scroll["'][\s\S]{0,120}syncDirectoryTop/,
  "滚动时也要重新量：控制区滚出视口后浮层该回到顶栏高度"
);
assert.match(
  js,
  /addEventListener\(\s*["']resize["'][\s\S]{0,120}syncDirectoryTop/,
  "改窗口宽度时也要重新量：筛选行会折行，高度会变"
);

// ---- 3. 宽屏规则没被动过 ----
const wide = css.match(/@media \(min-width: 1100px\) \{[\s\S]*?\n\}/);
assert.ok(wide, "library.css 里要有 ≥1100px 的宽屏规则");
assert.doesNotMatch(
  wide[0],
  /--library-directory-top/,
  "宽屏时侧栏在栅格列里，不该被窄屏的浮层变量牵走"
);

console.log("窄屏目录浮层不压筛选行：OK");
