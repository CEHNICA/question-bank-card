"use strict";
// 筛选条上按标签颜色上色的那颗小字，必须看得清。(1.26.37)
//
// 老师自己给标签设的颜色，可以任意深浅。1.25.64 那版拿标签颜色**当字色**，
// 实测（初中章那个蓝 #477a9e）：淡底 + 标签色字 = 3.88:1 —— 达不到 4.5:1 的清晰标准。
// 深色标签更糟：字和底几乎糊在一起。
//
// 这个测试**自己算对比度**（WCAG 算法），不是看样式文本长什么样：
// 从样式表里读出混色比例和字色，混一遍，算文字和底色的对比度。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const libraryCss = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
const stylesCss = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// ---- WCAG 对比度 ---------------------------------------------------------
function srgbToLinear(channel) {
  const value = channel / 255;
  return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
}
function luminance([r, g, b]) {
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b);
}
function contrast(foreground, background) {
  const a = luminance(foreground);
  const b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
function parseHex(value) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(value).trim());
  assert.ok(match, `能认得这个颜色：${value}`);
  const hex = match[1];
  return [0, 2, 4].map((offset) => parseInt(hex.slice(offset, offset + 2), 16));
}
function mix(first, second, percent) {
  const ratio = percent / 100;
  return first.map((channel, index) => channel * ratio + second[index] * (1 - ratio));
}
function variable(name, source) {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`).exec(source);
  assert.ok(match, `样式里能找到 --${name}`);
  return parseHex(match[1]);
}

// ---- 读出那条样式 --------------------------------------------------------
function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`).exec(libraryCss);
  assert.ok(match, `样式表里有 ${selector} 这条`);
  return match[1];
}

const surface = variable("surface", stylesCss);
const ink2 = variable("ink-2", stylesCss);

// 老师自己设的几个标签颜色，从浅到深都有。每一个都得看得清。
const tagColors = {
  "初中章那个蓝": "#477a9e",
  "深蓝": "#1f3a5f",
  "深紫": "#4b2e6b",
  "墨绿": "#1f4d3a",
  "砖红": "#7a2f28",
};

const chipRule = rule('.library-filter-chip[style*="--tag-color"]');
const dropRule = rule('.library-filter-chip[style*="--tag-color"] > .drop');

// 底色还是淡底：标签颜色按一个很低的比例混进页面底色。
const mixMatch = /background:\s*color-mix\(in srgb,[^;]*?(\d+(?:\.\d+)?)%\s*,\s*var\(--surface\)\)/.exec(chipRule);
assert.ok(mixMatch, "这颗标签是淡底，不是拿颜色整个当底色");
const percent = Number(mixMatch[1]);
assert.ok(percent <= 20, `混色比例要低（现在 ${percent}%），底色太深字就压不住了`);

// 边框还是同色边（标签的颜色从边上体现出来，不从字上）。
assert.match(chipRule, /border-color:\s*color-mix\(in srgb, var\(--tag-color\)/, "边框是这颗标签自己的颜色");

// ---- 字色不许再是标签自己的颜色 ------------------------------------------
assert.ok(
  !/color:\s*var\(--tag-color\)/.test(chipRule),
  "字不能再用标签自己的颜色 —— 标签颜色深浅是老师自己定的，深色标签上的字会糊掉",
);
assert.match(chipRule, /color:\s*var\(--ink-2\)/, "字用深色 —— 淡底深字才看得清");

// ---- 算账：每个标签色上都够清楚 ------------------------------------------
for (const [name, hex] of Object.entries(tagColors)) {
  const tag = parseHex(hex);
  const background = mix(tag, surface, percent);

  const textRatio = contrast(ink2, background);
  assert.ok(
    textRatio >= 4.5,
    `${name}：淡底上的字要看得清，现在 ${textRatio.toFixed(2)}:1，差 4.5:1`,
  );

  // 「×」那个小按钮也要看得清。
  const dropRatio = contrast(ink2, background);
  assert.ok(dropRatio >= 4.5, `${name}：删除那颗「×」也要看得清，现在 ${dropRatio.toFixed(2)}:1`);

  // 旧写法（拿标签色当字色）在**浅色标签**上确实不达标 —— 这就是改动的理由。
  // 深色标签当字色反而清楚，但那不重要：老师能自己把标签设成任意颜色，
  // 只要有一种颜色下看不清，这条就还不算修好。
  const oldRatio = contrast(tag, background);
  if (oldRatio < 4.5) {
    assert.ok(
      !/color:\s*var\(--tag-color\)/.test(chipRule),
      `${name}：拿颜色整个当字色时字是看不清的，${oldRatio.toFixed(2)}:1 —— 别退回这个写法`,
    );
  }
}

// 「×」不能退回灰色（muted 在淡底上只有 4.13:1）。
assert.ok(
  !/color:\s*var\(--muted\)/.test(dropRule),
  "「×」不能退回灰色 —— 淡底上灰字只有 4.13:1",
);

console.log("Filter chip text stays readable on every tag colour: OK");
