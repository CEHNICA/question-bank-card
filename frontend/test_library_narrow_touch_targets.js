"use strict";
// 经典题库页（frontend/index.html + styles.css）：切题阶段面板的错误条压字 +
// 窄屏小触摸目标 —— 静态结构守护。
//
// 背景（1440 / 390 实测）：
//   ① #cutReadingDetail 与 #cutReadingError 同为 .cut-reading-stage 的直接子 <p>，
//      被同一条 `.cut-reading-stage > p { grid-area: detail }` 塞进同一个网格格子，
//      两个元素 rect 完全重合（x321 y161 609×17）→ 红色错误提示直接压在说明文字上，
//      宽窄屏都有。修法：错误条与下面的「设置读题服务密钥」链接一起走自动排布，
//      在左列依次堆叠（grid-area: auto + grid-column: 1）。
//   ② 窄屏（390px）13 个触摸目标不足 32px：章节目录开关 23px、章节行 30px、
//      已归档试卷 31px、试卷操作 28px、设置读题服务密钥 20px、筛选芯片 31px。
//
// 硬要求：触摸目标的修法只允许出现在 @media 里 —— 宽屏（>760px）布局必须零变化。
// 唯独 ① 错误条是宽窄屏都存在的真 bug，必须是无条件规则。
//
// 看门狗自证：把修复前的 styles.css 喂给本脚本必须变红 ——
//   node test_library_narrow_touch_targets.js --css=<修复前的 styles.css>
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const argv = process.argv.slice(2);
const cssArg = (() => {
  const hit = argv.find((a) => a.startsWith("--css="));
  if (!hit) return null;
  return path.resolve(hit.slice("--css=".length));
})();
const cssPath = cssArg || path.join(__dirname, "styles.css");
const css = fs.readFileSync(cssPath, "utf8");

// ---- 工具：抽取 @media 块 / 剥掉所有 @media 块 -------------------------------

/** 取出所有 "@media <cond> { ... }" 的条件名与块体（花括号配对，支持嵌套） */
function extractMediaBlocks(src) {
  const out = [];
  const re = /@media([^{]+)\{/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    let depth = 1;
    let i = re.lastIndex;
    const start = i;
    while (i < src.length && depth > 0) {
      const ch = src[i];
      if (ch === "{") depth++;
      else if (ch === "}") depth--;
      i++;
    }
    out.push({ condition: m[1].trim(), body: src.slice(start, i - 1) });
    re.lastIndex = i;
  }
  return out;
}

/** 剥掉所有 @media 块，只剩「宽屏无条件生效」的规则 */
function stripMediaBlocks(src) {
  const blocks = extractMediaBlocks(src);
  let out = src;
  for (let k = blocks.length - 1; k >= 0; k--) {
    const cond = blocks[k].condition;
    const idx = out.indexOf("@media " + cond + "{");
    const idx2 = idx >= 0 ? idx : out.indexOf("@media " + cond);
    if (idx2 < 0) continue;
    let depth = 1;
    let i = out.indexOf("{", idx2) + 1;
    while (i < out.length && depth > 0) {
      const ch = out[i];
      if (ch === "{") depth++;
      else if (ch === "}") depth--;
      i++;
    }
    out = out.slice(0, idx2) + out.slice(i);
  }
  return out;
}

const mediaBlocks = extractMediaBlocks(css);
const wideRules = stripMediaBlocks(css);

/** 同一条件可能有多个 @media 块，合并后一起断言 */
function mediaBody(cond) {
  const hits = mediaBlocks.filter((b) => b.condition.includes(cond));
  assert.ok(hits.length > 0, `缺少 @media (${cond}) 媒体查询块`);
  return hits.map((b) => b.body).join("\n");
}

/** 断言某条规则出现在指定媒体查询块内 */
function assertInMedia(cond, ruleRe, label) {
  assert.match(mediaBody(cond), ruleRe, `${label} —— 应写在 @media (${cond}) 里`);
}

// ---- ① 切题阶段面板：错误条不得与详情段挤在同一个网格格子 --------------------
//
// `.cut-reading-stage > p` 这条规则无差别命中 #cutReadingDetail 与
// #cutReadingError，两者 grid-area 都是 detail → rect 完全重合，红字压住说明。

/** 只报短消息，不把整份 CSS 打进断言错误里 */
function has(re, text, label) {
  assert.ok(re.test(text), label);
}

has(
  /\.cut-reading-stage\s+\.cut-reading-error\s*\{[^}]*grid-area:\s*auto/,
  css,
  "切题阶段错误条必须脱离 grid-area: detail（否则与详情段完全重叠）"
);
has(
  /\.cut-reading-stage\s+\.cut-reading-error\s*\{[^}]*grid-column:\s*1/,
  css,
  "切题阶段错误条必须固定在左列（grid-column: 1），否则会被挤到右侧动作列"
);
has(
  /\.cut-reading-stage\s*>\s*a\s*\{[^}]*grid-area:\s*auto[^}]*grid-column:\s*1/,
  css,
  "切题阶段「设置读题服务密钥」链接必须与错误条同列堆叠"
);

// 这是宽窄屏都存在的真 bug，修复规则必须无条件生效（不能塞进媒体查询）
has(
  /\.cut-reading-stage\s+\.cut-reading-error\s*\{[^}]*grid-area:\s*auto/,
  wideRules,
  "错误条脱离 detail 格子的修复必须是无条件规则，宽屏同样压字"
);

// ---- ② 窄屏触摸目标抬到 32px -------------------------------------------------

const TOUCH_RULES = [
  [".entry-chapter-toggle", "章节目录开关"],
  [".entry-chapter-link", "章节目录行"],
  ["#archivedPapersButton", "已归档的试卷"],
  ["#paperMenuTrigger", "试卷操作菜单"],
  ["\\.cut-reading-stage\\s*>\\s*a", "设置读题服务密钥链接"],
  ["\\.filter", "筛选芯片"]
];
for (const [sel, label] of TOUCH_RULES) {
  assertInMedia(
    "max-width: 760px",
    new RegExp(sel + "\\s*[,{][^}]*min-height:\\s*32px"),
    `${label} 触摸目标抬到 32px`
  );
}

// ---- ③ 宽屏零变化：32px 抬升只许出现在媒体查询块内 ----------------------------

for (const [sel, label] of TOUCH_RULES) {
  assert.ok(
    !new RegExp(sel + "[^{}]*\\{[^}]*min-height:\\s*32px").test(wideRules),
    `${label} 的 32px 抬升只允许出现在窄屏媒体查询里，宽屏不得有残留规则`
  );
}

// ---- ④ 原卷画布 / 原卷查看器：窄屏小按钮抬到 32px -----------------------------
//
// 390px 实测（真实打开弹窗，不是 showModal 空开）：
//   · pageDialog（原卷画布）缩放条 4 个按钮只有 28~30px 高，
//     翻页栏 #pagePrevious / #pageNext / 手动裁切 / 布局加块 / 撤销 / 重试
//     都是 .button.small = 28px；
//   · viewerDialog（原卷查看器）缩放条 4 个按钮同样 28px 高。
// → 全部在 @media (max-width: 540px) 里抬到 32px（宽屏零变化）。

const CANVAS_RULES = [
  ["\\.page-dialog\\s+\\.zoom-controls\\s+\\.button", "min-height:\\s*32px", "原卷画布缩放条按钮"],
  ["\\.page-dialog\\s+\\.zoom-controls\\s+\\.button", "min-width:\\s*32px", "原卷画布缩放条按钮"],
  ["\\.page-dialog\\s+\\.button\\.small", "min-height:\\s*32px", "原卷画布翻页/裁切等小按钮"],
  ["\\.viewer-dialog\\s+\\.zoom-controls\\s+\\.button", "min-height:\\s*32px", "原卷查看器缩放条按钮"],
  ["\\.viewer-dialog\\s+\\.zoom-controls\\s+\\.button", "min-width:\\s*32px", "原卷查看器缩放条按钮"]
];

for (const [sel, size, label] of CANVAS_RULES) {
  assertInMedia(
    "max-width: 540px",
    new RegExp(sel + "\\s*[,{][^}]*" + size),
    `${label} 触摸目标抬到 32px`
  );
}

// 宽屏不得有残留：.page-dialog / .viewer-dialog 的 32px 抬升只能在窄屏媒体查询里
for (const [sel] of CANVAS_RULES) {
  assert.ok(
    !new RegExp(sel + "[^{}]*\\{[^}]*min-(height|width):\\s*32px").test(wideRules),
    `${sel} 的 32px 抬升只允许出现在窄屏媒体查询里，宽屏不得有残留规则`
  );
}

console.log(`经典题库页切题面板 + 窄屏触摸目标静态检查：OK（${cssPath}）`);
