"use strict";
// Eagle：Tailwind 必须本地化，不得再依赖 cdn.tailwindcss.com 外网 CDN。
// 原因：外网 CDN 在断网/墙内环境下会把首屏 DCL 拖到 2.6 秒以上，且 Tailwind 样式
// 全部落空（页面无样式）。本地预编译产物随前端目录走，离线可用。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");
const cssPath = path.join(__dirname, "eagle", "tailwind.css");

// 1) 页面不许再引用外网 Tailwind CDN
assert.doesNotMatch(html, /cdn\.tailwindcss\.com/, "Eagle 页不得引用外网 Tailwind CDN");

// 2) 必须改用本地样式表
assert.match(html, /<link[^>]+href="\/eagle\/tailwind\.css/, "Eagle 页必须引用本地 /eagle/tailwind.css");

// 3) 本地产物必须存在且是真正的 Tailwind 编译产物
assert.ok(fs.existsSync(cssPath), "frontend/eagle/tailwind.css 必须存在");
const css = fs.readFileSync(cssPath, "utf8");
assert.ok(css.length > 10000, `tailwind.css 体积异常小: ${css.length}`);
assert.match(css, /--tw-/, "tailwind.css 必须是 Tailwind 编译产物");
assert.match(css, /\.flex\{/, "tailwind.css 必须包含基础工具类");
assert.match(css, /\.rounded-lg\{/, "tailwind.css 必须包含圆角工具类");

// 4) 预编译产物必须覆盖 Eagle 页真正用到的几个类（缺一个就是样式塌了）
for (const cls of [
  "bg-emerald-50",
  "text-slate-600",
  "border-amber-200\\/70",
  "rotate-180",
  "min-h-\\[110px\\]",
]) {
  assert.ok(css.includes(cls), `tailwind.css 缺少 ${cls}`);
}

// 5) 产物是编译快照，不允许内嵌 @import 外链
assert.doesNotMatch(css, /@import\s+url\(https?:/, "tailwind.css 不得再向外链 CSS");
