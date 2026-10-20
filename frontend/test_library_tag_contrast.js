"use strict";
// 贴在题上的那颗标签，底色不能深到把字压住。(1.25.95)
//
// 老师 10 月 21 日零点发来一张截图：「把字体挡住了，颜色太深」—— 指的是「整理勾选题的
// 标签」窗口里「已经在这道题上」的那两颗。
//
// 查下来：那条样式把标签的颜色**整个当底色**用（background: var(--tag-color)）。
// 1.25.93 之前这颗 chip 上没有 --tag-color，底色落回浅灰，看着正常；1.25.93 统一给
// 标签上色之后，它就变成「深蓝底 + 近黑的字」。
// 实测（真实程序，1.25.94）：底色 rgb(71,122,158)、字 rgb(31,35,32) —— 对比度只有 1.8:1。
//
// 这一版改成跟题卡、题目详情那几处一样：淡底 + 同色边 + 深字。
// 这个测试**自己算对比度**（按 WCAG 的算法），不是看规则文本长什么样：
// 把那条样式里的混色比例读出来，混一遍，算文字和底色的对比度。
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
const tagBlue = parseHex("#477a9e");   // 老师的初中章自己设的那个蓝

const chipRule = rule(".tag-picker-current-chip");

// 底色必须是「淡底」：标签的颜色按一个很低的比例混进页面底色里，
// 而不是把标签的颜色整个当底色。
const mixMatch = /background:\s*color-mix\(in srgb,[^;]*?(\d+(?:\.\d+)?)%\s*,\s*var\(--surface\)\)/.exec(chipRule);
assert.ok(mixMatch, "这颗标签是淡底（标签的颜色按一个很低的比例混进底色），不是拿颜色整个当底色");
const percent = Number(mixMatch[1]);
assert.ok(percent <= 20, `混色比例要低（现在 ${percent}%），底色太深字就压不住了`);

const borderMatch = /border:\s*1px solid var\(--tag-color/.exec(chipRule);
assert.ok(borderMatch, "边框是这颗标签自己的颜色（同色边）");
assert.match(chipRule, /color:\s*var\(--ink-2\)/, "字是深色 —— 浅底深字才看得清");

// ---- 算账：新的写法够不够清楚，旧写法够不够清楚 -------------------------
const newBackground = mix(tagBlue, surface, percent);
const newRatio = contrast(ink2, newBackground);
const oldRatio = contrast(ink2, tagBlue);

assert.ok(newRatio >= 4.5,
  `淡底上黑字要看得清：现在 ${newRatio.toFixed(2)}:1，差 4.5:1`);
assert.ok(oldRatio < 4.5,
  `拿颜色整个当底色时字是看不清的：${oldRatio.toFixed(2)}:1 —— 这就是老师报的毛病`);

// ---- 题卡和题目详情那两颗也是同一套 --------------------------------------
for (const [name, selector] of [
  ["题卡上那颗", ".library-card-tag-glimpse"],
  ["题目详情里那颗", ".library-tag-chip"],
]) {
  const css = rule(selector);
  assert.match(css, /color-mix\(/, `${name}：也是淡底`);
  assert.match(css, /var\(--tag-color/, `${name}：用的是这颗标签自己的颜色`);
}

// ---- 不许再有拿颜色当底色的「文字块」 ------------------------------------
for (const match of libraryCss.matchAll(/([^{}]+)\{([^}]*background:\s*var\(--tag-color[^;]*;)/g)) {
  const selector = match[1].trim().split("\n").pop().trim();
  // 圆点本来就该是纯色 —— 它上面没有字。
  assert.match(selector, /dot$/,
    `「${selector}」把标签颜色整个当底色了 —— 上面有字的地方不能这么干（会像 1.25.94 那样把字压住）`);
}

console.log("Tags on a question: a light tint with dark text, contrast " + newRatio.toFixed(2) + ":1 "
  + "(the old way was " + oldRatio.toFixed(2) + ":1): OK");
