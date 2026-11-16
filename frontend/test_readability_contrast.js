"use strict";
// 浅灰小字看不清 —— 对比度体检的守护测试。
//
// 这批是实测（Playwright + 合成有效背景色算出来的）真缺陷，不是审美问题：
//
//   经典题库页（frontend/styles.css）
//     .side-heading         「待录入」「3」      #94a3b8 on #fff = 2.56:1
//     .filter-count         筛选芯片上的计数     #94a3b8 on #f1f5f9 = 2.34:1
//     .entry-chapter-total  「72 节」            #94a3b8 on #fff = 2.56:1
//     .card-tick            题卡勾选框           #94a3b8 on #fff = 2.56:1
//     --amber               「原图题 · 待核对」   #d97706 on #fffbeb = 3.07:1
//     .filter.attention .filter-count  白字在琥珀条上 = 2.15:1
//     --muted               次要文字令牌         #64748b on #f1f5f9 = 4.34:1
//     .filter               工具条筛选按钮文字   #64748b on #f1f5f9 = 4.34:1
//
//   题库工作台（frontend/eagle/index.html）
//     卡片来源文件名 / 目录计数 / 滚动提示   slate-400 on #fff = 2.56:1
//     标签上的「×」（唯一删除入口）        emerald-400 on #fff = 1.84:1
//     右侧属性栏「评分:」「OneNote:」等    slate-400 on #fafcfd = 2.48:1
//     右侧属性栏未点亮评分星              slate-300 on #fafcfd = 1.44:1
//     「点标签即采纳」                    emerald-700/80 = 3.68:1
//
// WCAG 2.1 AA：普通文字 4.5:1，大字（≥24px 或 ≥18.66px 粗体）3:1。
//
// 这个测试**自己算对比度**（WCAG 算法），不是看样式文本长什么样 —— 颜色写对了但
// 算出来不达标，一样要红。喂修复前的样式（--css= / --html=）进来必须变红。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
}

const cssPath = arg("css") || path.join(__dirname, "styles.css");
const htmlPath = arg("html") || path.join(__dirname, "eagle", "index.html");
const stylesCss = fs.readFileSync(cssPath, "utf8");
const eagleHtml = fs.readFileSync(htmlPath, "utf8");

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
  const match = /^#?([0-9a-fA-F]{6})$/.exec(String(value).trim());
  assert.ok(match, `能认得这个颜色：${value}`);
  const hex = match[1];
  return [0, 2, 4].map((offset) => parseInt(hex.slice(offset, offset + 2), 16));
}
function mixHex(hex, alpha, over) {
  const base = parseHex(hex);
  const under = parseHex(over);
  return base.map((channel, index) => channel * alpha + under[index] * (1 - alpha));
}

// ---- 从样式表里读出「最后一条同名规则」的值（层叠：同级后者胜） ---------
function escapeRe(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function lastRule(css, selector) {
  const re = new RegExp(`(?:^|[}\\n])\\s*${escapeRe(selector)}\\s*\\{([^}]*)\\}`, "g");
  let body = null;
  let hit;
  while ((hit = re.exec(css)) !== null) body = hit[1];
  assert.ok(body !== null, `样式表里有 ${selector} 这条规则`);
  return body;
}
function declOf(body, property) {
  const match = new RegExp(`(?:^|[;{\\s])${escapeRe(property)}\\s*:\\s*([^;]+)`).exec(body);
  return match ? match[1].trim() : null;
}
function lastVar(css, name) {
  const re = new RegExp(`--${escapeRe(name)}\\s*:\\s*(#[0-9a-fA-F]{6})`, "g");
  let value = null;
  let hit;
  while ((hit = re.exec(css)) !== null) value = hit[1];
  assert.ok(value !== null, `样式里能找到 --${name}`);
  return value;
}
function resolve(css, value) {
  if (!value) return null;
  const varMatch = /^var\(\s*--([a-z0-9-]+)\s*\)$/i.exec(value);
  if (varMatch) return lastVar(css, varMatch[1]);
  const hexMatch = /^#[0-9a-fA-F]{6}$/.exec(value);
  if (hexMatch) return value;
  const short = /^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$/.exec(String(value).trim());
  if (short) return `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}`;
  return null;
}

const AA = 4.5;

function assertContrast(css, selector, property, background, need, label) {
  const body = lastRule(css, selector);
  const raw = declOf(body, property);
  const hex = resolve(css, raw);
  assert.ok(hex, `${label}：${selector} 的 ${property} 得是能算的颜色，实际是「${raw}」`);
  const bg = Array.isArray(background) ? background : [background];
  for (const under of bg) {
    const ratio = contrast(parseHex(hex), parseHex(under));
    assert.ok(
      ratio >= need,
      `${label}：${selector} 的 ${property}=${hex} 在 ${under} 上只有 ${ratio.toFixed(2)}:1，` +
      `达不到 ${need}:1 的清晰标准`
    );
  }
}

// ---- 经典题库页 ----------------------------------------------------------
// 琥珀色整族：--amber 当字色用在琥珀浅底上，也当徽章底配白字。
const amber = lastVar(stylesCss, "amber");
assertContrast(stylesCss, ".chip.yellow", "color", "#fffbeb", AA, "「原图题 · 待核对」徽章");
assertContrast(stylesCss, ".side-heading", "color", ["#ffffff", "#f8fafc"], AA, "侧栏小标题");
assertContrast(stylesCss, ".filter-count", "color", ["#f1f5f9", "#ffffff"], AA, "筛选芯片计数");
assertContrast(stylesCss, ".entry-chapter-total", "color", ["#ffffff"], AA, "章节行「72 节」");
assertContrast(stylesCss, ".card-tick", "color", ["#ffffff"], AA, "题卡勾选框");
// 「需要核查（509）」那颗琥珀计数：白字压在琥珀条上
{
  const body = lastRule(stylesCss, ".filter.attention .filter-count");
  const bg = resolve(stylesCss, declOf(body, "background"));
  const fg = resolve(stylesCss, declOf(body, "color"));
  assert.ok(bg && fg, "「需要核查」计数徽章的底色字色得能算");
  const ratio = contrast(parseHex(fg), parseHex(bg));
  assert.ok(ratio >= AA, `「需要核查」计数徽章 ${fg} on ${bg} 只有 ${ratio.toFixed(2)}:1`);
  // 底色必须跟着 --amber 走，别写死一个跟琥珀条不同步的浅色
  assert.equal(bg, amber, "「需要核查」计数的底色应当用 var(--amber)，跟着令牌走");
}
// --amber 自身在白底和浅底上都得站得住
{
  const ratio = contrast(parseHex(amber), parseHex("#ffffff"));
  assert.ok(ratio >= AA, `--amber=${amber} 在白底上只有 ${ratio.toFixed(2)}:1`);
}
// 次要文字令牌 --muted：压在 sunken 底（#f1f5f9）上也要过 AA
{
  const muted = lastVar(stylesCss, "muted");
  for (const under of ["#ffffff", "#f8fafc", "#f1f5f9"]) {
    const ratio = contrast(parseHex(muted), parseHex(under));
    assert.ok(ratio >= AA, `--muted=${muted} 在 ${under} 上只有 ${ratio.toFixed(2)}:1`);
  }
}
// 工具条上的筛选按钮文字：透明底 → 落在工具条底色（--bg-sunken）上
assertContrast(stylesCss, ".filter", "color", ["#f1f5f9", "#ffffff"], AA, "工具条筛选按钮文字");

// ---- 题库工作台（Eagle）-------------------------------------------------
{
  // 自定义覆盖规则必须在（Tailwind 是预编译产物，只能靠这条盖过去）
  const hasSlate = /\.text-slate-400\s*\{[^}]*color:\s*#5b6574;/.test(eagleHtml);
  assert.ok(hasSlate, "Eagle 页要有把 slate-400 整体抬到 #5b6574 的覆盖规则");
  assert.ok(
    /#batchActionBar\s+\.text-slate-400\s*\{[^}]*color:\s*#cbd5e1;/.test(eagleHtml),
    "Eagle 深色批量操作条上的浅灰字要单独留浅色，不能跟着变灰"
  );
  const hasStars = /#metaDifficultyStars\s+\.text-slate-300\s*\{[^}]*color:\s*#5b6574;/.test(eagleHtml);
  assert.ok(hasStars, "Eagle 右侧属性栏的未点亮评分星要抬到 #5b6574（原来 1.44:1）");
  const hasFavStars = /button\[id\^="star_btn_"\]\.text-slate-300/.test(eagleHtml);
  assert.ok(hasFavStars, "Eagle 列表里的未收藏星要抬到 #5b6574");
  const hasEmerald = /button\.text-emerald-400\s*\{[^}]*color:\s*#047857;/.test(eagleHtml);
  assert.ok(hasEmerald, "Eagle 页标签上的「×」要提到 emerald-700");
  const hasHint = /#inspectorBody\s+\.text-emerald-700\\\/80\s*\{[^}]*color:\s*#047857;/.test(eagleHtml);
  assert.ok(hasHint, "Eagle「点标签即采纳」要去掉 emerald-700/80 的透明度（原来 3.68:1）");
  // 颜色本身得达标：#5b6574 在白 / slate-50 / slate-100 上都要过 AA
  for (const under of ["#ffffff", "#f8fafc", "#f1f5f9"]) {
    const ratio = contrast(parseHex("#5b6574"), parseHex(under));
    assert.ok(ratio >= AA, `#5b6574 在 ${under} 上只有 ${ratio.toFixed(2)}:1`);
  }
  const ratioGreen = contrast(parseHex("#047857"), parseHex("#ffffff"));
  assert.ok(ratioGreen >= AA, `#047857 在白底上只有 ${ratioGreen.toFixed(2)}:1`);
}

// 第二轮体检（静态色对扫描）挖出来的一批：字色压浅底，全都不到 AA 的 4.5:1。
// 这些是浏览器扫不到的坑 —— 默认 hidden、hover 才变色、或者在别的容器底上。
const EAGLE_TEXT_RULES = [
  // [选择器, 背景列表, 说明]
  [".text-slate-300", ["#ffffff", "#f8fafc", "#f1f5f9"], "排序菜单未选中项 / 树里「（当前目录）」「（超过N层上限）」/ 题干预览占位符"],
  [".text-slate-400", ["#ffffff", "#f8fafc", "#f1f5f9"], "题卡来源文件名 / 目录计数 / 属性栏小字"],
  [".text-slate-500", ["#ffffff", "#f8fafc", "#f1f5f9", "#e2e8f0"], "次级文字"],
  [".text-amber-500", ["#ffffff", "#f8fafc"], "评分星 / 「未评分」"],
  [".text-amber-600", ["#ffffff", "#f8fafc"], "主菜单「AI生成未人工核对」的计数徽章"],
  [".text-emerald-600", ["#ffffff", "#f8fafc"], "标签「启用」/ 次级强调"],
  [".text-rose-400", ["#ffffff", "#f8fafc"], "上下文菜单的 Delete 键帽"],
  [".text-rose-500", ["#ffffff", "#f8fafc", "#fff1f2"], "「清空试题篮」/「停用」小字按钮"],
  [".text-rose-600", ["#ffffff", "#f8fafc", "#fff1f2"], "导出状态行报错文字 / 上下文菜单红字"],
  ["button.text-emerald-400", ["#ecfdf5", "#ffffff"], "标签条上的「×」删除入口"],
  ["button.text-emerald-500", ["#ecfdf5", "#ffffff"], "文件夹条上的「×」移出入口"],
];
for (const [selector, backgrounds, label] of EAGLE_TEXT_RULES) {
  assertContrast(eagleHtml, selector, "color", backgrounds, AA, label);
}

// 白字压浅色底（徽章底 + 主操作按钮）：底色整族抬到 -700，白字才够 4.5:1
const EAGLE_BADGE_BG = [
  [".bg-emerald-500", "#047857"],
  [".bg-emerald-600", "#047857"],
  [".bg-amber-500", "#b45309"],
  [".bg-amber-600", "#b45309"],
  [".bg-teal-500", "#0f766e"],
  [".bg-sky-500", "#0369a1"],
  [".bg-violet-500", "#6d28d9"],
];
for (const [selector, expected] of EAGLE_BADGE_BG) {
  const body = lastRule(eagleHtml, selector);
  const bg = resolve(eagleHtml, declOf(body, "background-color"));
  assert.equal(bg, expected, `${selector} 的底色应当是 ${expected}`);
  const ratio = contrast(parseHex("#ffffff"), parseHex(bg));
  assert.ok(ratio >= AA, `${selector} 底色 ${bg} 配白字只有 ${ratio.toFixed(2)}:1`);
}
// 深色批量操作条上的玫瑰红图标（rose-400 on slate-900）是非文字控件，只要 3:1
{
  const body = lastRule(eagleHtml, "#batchActionBar .text-rose-400");
  const fg = resolve(eagleHtml, declOf(body, "color"));
  const ratio = contrast(parseHex(fg), parseHex("#0f172a"));
  assert.ok(ratio >= 3, `深色批量条上的玫瑰红图标 ${fg} on #0f172a 只有 ${ratio.toFixed(2)}:1`);
}

// 输入框占位符：Tailwind preflight 把 ::placeholder 设成 slate-400（2.56:1）/ gray-400（2.54:1），
// 白底上根本看不清该填什么 → 抬到 #5b6574（5.90:1）
{
  const ok = /input::placeholder,\s*textarea::placeholder,\s*\n?\s*\.placeholder-slate-400::placeholder\s*\{[^}]*color:\s*#5b6574;/.test(eagleHtml);
  assert.ok(ok, "Eagle 页的输入框占位符要抬到 #5b6574（原来 2.56:1 / 2.54:1）");
  const ratio = contrast(parseHex("#5b6574"), parseHex("#ffffff"));
  assert.ok(ratio >= AA, `占位符色 #5b6574 在白底上只有 ${ratio.toFixed(2)}:1`);
}

console.log("浅灰小字都看得清了（WCAG AA 对比度）：OK");
