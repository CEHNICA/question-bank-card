"use strict";
// 题卡「更多」菜单与全界面现代 Clean UI 校验
// 1. 题库页题卡必须放开 overflow，确保点击「更多」能正常弹出下拉菜单；
// 2. 展开「更多」时必须提升 z-index，防止下拉框被下方题卡遮挡；
// 3. 全局 styles.css 与 library.css 设计规范与组件完备性。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const libraryCss = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
const stylesCss = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// 1. 验证题库页题卡没有 overflow: hidden，且展开时有层级抬升
const libraryCardBlockMatch = /\.library-page\s+\.library-card\s*\{([^}]*)\}/.exec(libraryCss);
assert.ok(libraryCardBlockMatch, "library.css 中存在 .library-page .library-card 样式块");
const libraryCardBlock = libraryCardBlockMatch[1];
assert.ok(
  !libraryCardBlock.includes("overflow: hidden"),
  ".library-page .library-card 不能包含 overflow: hidden，否则「更多」下拉菜单会被截断消失",
);
assert.ok(
  libraryCardBlock.includes("overflow: visible"),
  ".library-page .library-card 必须为 overflow: visible，确保子菜单正常溢出展示",
);

// 验证展开层级抬升
assert.ok(
  /\.library-page\s+\.library-card:has\(\.library-card-more\[open\]\)\s*\{[^}]*z-index:\s*3[0-9]/.test(libraryCss),
  "展开 .library-card-more 时必须抬升卡片 z-index，避免被下方卡片遮挡",
);

// 验证更多菜单的弹出定位与 z-index
assert.ok(
  /\.library-card-menu\s*\{[^}]*position:\s*absolute/.test(libraryCss),
  "library-card-menu 必须使用绝对定位弹出",
);

// 2. 验证核对页卡片 (index.html) 的更多下拉菜单
assert.ok(
  /\.more-menu\s*\{[^}]*position:\s*absolute/.test(stylesCss),
  "styles.css 中 .more-menu 必须使用绝对定位",
);
assert.ok(
  /\.card:has\([^)]*details\.more\[open\]\)\s*\{[^}]*overflow:\s*visible/.test(stylesCss),
  "核对页展开 details.more 时卡片必须放开 overflow: visible",
);

// 3. 验证设置页与全局 Clean UI 样式规范
assert.ok(
  /\.settings-nav\s*\{[^}]*border-radius:/.test(stylesCss),
  "设置页 .settings-nav 具备现代圆角分段控制器规范",
);
assert.ok(
  /\.settings-section\s*\{[^}]*border-radius:/.test(stylesCss),
  "设置页 .settings-section 具备现代独立卡片规范",
);

console.log("Card dropdown and global clean UI checks: OK");
