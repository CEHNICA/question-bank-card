"use strict";
// Eagle：窄屏浮层（试题篮导出 / 标签管理 / 快捷键 / 快速审题 / 筛选胶囊）与
// 题卡、工具栏的小触摸目标 —— 静态结构守护。
//
// 背景（390×844 实测）：
//   ① 试题篮导出弹窗两栏并排，右栏只剩 142px，里面 6 个下拉框被压到 47px 宽，
//      选项文字全被挤没 → 窄屏改成上下堆叠 + 下拉框吃满栏宽。
//   ② 一批触摸目标不足 32px（弹窗 ✕ 11×20、标签「停用」20×16、筛选胶囊高 26px、
//      题名输入框高 16px、收藏星 18×18、题卡勾选框 14×14 …）→ 窄屏统一抬到
//      32px（复选框按 WCAG 2.5.8 AA 取 24px）。
//
// 硬要求：所有修法只允许出现在 @media 里 —— 宽屏（>768px）布局必须零变化。
// 因此本测试同时断言「规则在媒体查询块内」且「媒体查询块外不残留这些规则」。
//
// 看门狗自证：把 HEAD 版（修复前）的 index.html 喂给本脚本必须变红 ——
//   node test_eagle_narrow_overlay_touch_targets.js --html=<修复前的 index.html>
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const argv = process.argv.slice(2);
const htmlArg = (() => {
  const hit = argv.find((a) => a.startsWith("--html="));
  if (!hit) return null;
  return path.resolve(hit.slice("--html=".length));
})();
const htmlPath = htmlArg || path.join(__dirname, "eagle", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");

// ---- 工具：抽取 @media 块 / 剥离所有 @media 块 -------------------------------

/** 只留 <style> 块内容 —— CSS 判定不该被页面 JS 里的同名选择器字符串干扰 */
function stripNonStyle(src) {
  const out = [];
  const re = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
  let m;
  while ((m = re.exec(src)) !== null) out.push(m[1]);
  return out.join("\n");
}

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
  // 从后往前删，避免偏移错乱
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

const mediaBlocks = extractMediaBlocks(stripNonStyle(html));
const wideRules = stripMediaBlocks(stripNonStyle(html));

/** 同一条件可能有多个 @media 块（CSS 允许重复声明），这里合并后一起断言 */
function mediaBody(cond) {
  const hits = mediaBlocks.filter((b) => b.condition.includes(cond));
  assert.ok(hits.length > 0, `缺少 @media (${cond}) 媒体查询块`);
  return hits.map((b) => b.body).join("\n");
}

/** 断言某条规则出现在指定媒体查询块内 */
function assertInMedia(cond, ruleRe, label) {
  const body = mediaBody(cond);
  assert.match(body, ruleRe, `${label} —— 应写在 @media (${cond}) 里`);
}

// ---- ① 试题篮导出弹窗：窄屏两栏堆叠 ----------------------------------------

assertInMedia(
  "max-width: 768px",
  /#basketExportModal\s+\.bkm-body\s*\{[^}]*flex-direction:\s*column/,
  "试题篮导出弹窗两栏在窄屏改上下堆叠"
);
assertInMedia(
  "max-width: 768px",
  /#basketExportModal\s+\.bkm-col-right\s*\{[^}]*width:\s*100%/,
  "试题篮导出弹窗右栏整栏吃满宽度"
);
assertInMedia(
  "max-width: 768px",
  /#basketExportModal\s+select[^{]*\{[^}]*min-height:\s*32px/,
  "试题篮导出弹窗下拉框抬高到 32px"
);
assertInMedia(
  "max-width: 768px",
  /#basketExportModal\s+\.bkm-close\s*\{[^}]*min-width:\s*32px[^}]*min-height:\s*32px/,
  "试题篮导出弹窗关闭按钮 ≥32px"
);

// ---- ② 触摸目标：窄屏统一抬到 32px -----------------------------------------

const TOUCH_RULES = [
  [".ql-head-actions button", "min-height:\\s*32px", "快速审题头部按钮"],
  [".ql-foot-actions button", "min-height:\\s*32px", "快速审题底部按钮"],
  ["#qlNameInput", "min-height:\\s*32px", "快速审题题名输入框"],
  ["#tagManagerModal\\s+\\.tm-head\\s*>\\s*button", "min-width:\\s*32px", "标签管理头部按钮"],
  ["#tagManagerModal\\s+#modalTagList\\s+button", "min-width:\\s*32px", "标签管理列表按钮"],
  ["#shortcutsModal\\s+\\.sc-close", "min-width:\\s*32px", "快捷键弹窗关闭按钮"],
  ["#filterPillsPanel\\s+button\\[id\\^=\"pillBtn\"\\]", "min-height:\\s*32px", "筛选胶囊"],
  ["#basketExportModal\\s+\\.bkm-clear", "min-height:\\s*32px", "试题篮清空按钮"],
  ["#btnCollapsedAppMenu", "min-width:\\s*32px", "折叠态应用菜单按钮"],
  ["#btnToggleInspector", "min-width:\\s*32px", "属性栏开关按钮"],
  ["#questionListContainer\\s+button\\[id\\^=\"star_btn_\"\\]", "min-width:\\s*32px", "题卡收藏星"],
  ["#questionListContainer\\s+button\\[id\\^=\"btn_analysis_\"\\]", "min-height:\\s*32px", "题卡查看解析"],
  ["#questionListContainer\\s+button\\[id\\^=\"btn_basket_\"\\]", "min-height:\\s*32px", "题卡试题篮"],
  ["#questionListContainer\\s+button\\[title\\^=\"更多操作\"\\]", "min-width:\\s*32px", "题卡更多操作"],
  ["#questionListContainer\\s+input\\[type=\"checkbox\"\\]", "width:\\s*24px", "题卡勾选框（WCAG 2.5.8 AA）"]
];

for (const [sel, size, label] of TOUCH_RULES) {
  assertInMedia(
    "max-width: 640px",
    new RegExp(sel + "\\s*[,{][^}]*" + size),
    `${label} 触摸目标抬到 ${size.replace(/\\s*.*/, "")}`
  );
}

// ---- ②b 标签管理中心窄屏：群组导航别吃掉半个弹窗，标签名别丢字 ----------
// 390px 实测：左栏 #modalTagGroupsNav 占 176px（w-44），右列表只剩 148px，
// 标签名被 truncate 到 63px（「一元一次不等式」少 21px）；✕ 被 flex-wrap 挤到
// 标题第二行左侧。→ 窄屏改上下堆叠 + 标题不换行 + 标签名允许换行不截断。

assertInMedia(
  "max-width: 640px",
  /#tagManagerModal\s+\.tm-body\s*\{[^}]*flex-direction:\s*column/,
  "标签管理中心窄屏改上下堆叠"
);
assertInMedia(
  "max-width: 640px",
  /#tagManagerModal\s+#modalTagGroupsNav\s*\{[^}]*width:\s*100%/,
  "标签管理中心群组导航整栏吃满宽度"
);
assertInMedia(
  "max-width: 640px",
  /#tagManagerModal\s+\.tm-head\s*\{[^}]*flex-wrap:\s*nowrap/,
  "标签管理中心标题行不换行（✕ 留在右上角）"
);
assertInMedia(
  "max-width: 640px",
  /#tagManagerModal\s+#modalTagList\s+\.truncate\s*\{[^}]*white-space:\s*normal/,
  "标签名窄屏允许换行，不许 ellipsis 截断丢字"
);

// ---- ②c 顶栏 / 左栏工具条的小入口也别漏 -------------------------------------

assertInMedia(
  "max-width: 640px",
  /#btnEagleAppMenu[^{]*\{[^}]*min-height:\s*32px/,
  "顶栏应用菜单按钮抬到 32px"
);
assertInMedia(
  "max-width: 640px",
  /#treeSearchInput[^{]*\{[^}]*min-height:\s*32px/,
  "左栏树搜索框抬到 32px"
);
assertInMedia(
  "max-width: 640px",
  /#globalSearchInput[^{]*\{[^}]*min-height:\s*32px/,
  "顶栏搜索框抬到 32px"
);
assertInMedia(
  "max-width: 640px",
  /#btnToggleFilterPanel[^{]*\{[^}]*min-height:\s*32px/,
  "筛选面板入口按钮抬到 32px"
);
assertInMedia(
  "max-width: 640px",
  /(?:button\[title="打开试题篮"\]|#btnOpenBasket)[^{]*\{[^}]*min-height:\s*32px/,
  "顶栏试题篮入口按钮抬到 32px"
);

// ---- ②d 其余浮层（第三轮）：文件夹选择 / 删除确认 / 图片灯箱 / A4 试卷预览 / 原卷核验 ----
// （原 #solutionEditorModal 与 #editQuestionModal 已统一收口到 LibraryUnifiedEditor）

const MODAL_TOUCH = [
  ["#folderPickerModal .x-close", "文件夹选择弹窗关闭按钮"],
  ["#deleteConfirmModal .x-close", "删除确认弹窗关闭按钮"],
  ["#originalPaperModal .x-close", "原卷核验弹窗关闭按钮"],
  ["#folderPickerModal #folderPickerTreeList button", "文件夹树行"],
  ["#deleteConfirmModal #deleteConfirmBtn", "删除确认主按钮"],
  ["#originalPaperModal #opRawImgLink", "原卷大图链接"],
  ["#imageLightboxModal button", "图片灯箱按钮"],
  ["#basketPreviewModal button", "A4 试卷预览弹窗按钮"]
];
for (const [sel, label] of MODAL_TOUCH) {
  assertInMedia(
    "max-width: 640px",
    // 选择器可能是逗号合并列表（`#a .x-close,\n#b .x-close { ... }`），
    // 所以只要求「该选择器出现在同一条规则的选择器前段」，用 [^{}]* 跨过其余选择器。
    new RegExp(
      sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s*") +
        "[^{}]*\\{[^}]*min-(?:height|width):\\s*32px"
    ),
    `${label} 触摸目标抬到 32px`
  );
}

// ---- ③ 宽屏零变化：这些选择器不得出现在媒体查询块外 ------------------------

const WIDE_FORBIDDEN = [
  "#basketExportModal .bkm-body",
  "#basketExportModal .bkm-col-left",
  "#basketExportModal .bkm-col-right",
  "#basketExportModal .bkm-close",
  "#basketExportModal .bkm-clear",
  "#shortcutsModal .sc-close",
  "#filterPillsPanel button[id^=\"pillBtn\"]",
  "#questionListContainer button[id^=\"star_btn_\"]",
  "#questionListContainer input[type=\"checkbox\"]",
  "#selectAllCheckbox",
  "#tagManagerModal .tm-body",
  "#tagManagerModal #modalTagGroupsNav",
  "#tagManagerModal .tm-head",
  "#tagManagerModal #modalTagList .truncate",
  "#folderPickerModal .x-close",
  "#deleteConfirmModal .x-close",
  "#originalPaperModal .x-close",
  "#imageLightboxModal button",
  "#basketPreviewModal button"
];
for (const sel of WIDE_FORBIDDEN) {
  assert.ok(
    !wideRules.includes(sel),
    `${sel} 只允许出现在窄屏媒体查询里，宽屏不得有残留规则`
  );
}

// ---- ④ HTML class 钩子必须存在（CSS 靠它们定位） ---------------------------

const HOOKS = [
  ["bkm-body", "试题篮导出弹窗主体"],
  ["bkm-col-left", "试题篮导出弹窗左栏"],
  ["bkm-col-right", "试题篮导出弹窗右栏"],
  ["bkm-close", "试题篮导出弹窗关闭按钮"],
  ["bkm-clear", "试题篮导出弹窗清空按钮"],
  ["sc-close", "快捷键弹窗关闭按钮"]
];
for (const [cls, label] of HOOKS) {
  assert.match(
    html,
    new RegExp(`class="[^"]*\\b${cls}\\b[^"]*`),
    `${label} 缺少 class 钩子 ${cls}`
  );
}

// 关闭按钮钩子 x-close 必须覆盖现存弹窗（窄屏下统一抬到 32px）
for (const [modal, label] of [
  ["folderPickerModal", "文件夹选择"],
  ["deleteConfirmModal", "删除确认"],
  ["originalPaperModal", "原卷核验"]
]) {
  const block = html.slice(html.indexOf(`id="${modal}"`));
  const end = block.indexOf("\n  </div>");
  const scope = end > 0 ? block.slice(0, end) : block;
  assert.match(scope, /class="[^"]*\bx-close\b[^"]*"/, `${label}弹窗缺少 x-close 钩子`);
}

// ---- ⑤ 窄屏关闭/清空按钮不许再是裸 Tailwind 尺寸类 ------------------------
// （若有人把钩子删了只留 Tailwind class，本断言会红）

assert.match(
  html,
  /#basketExportModal\s+\.bkm-close\s*\{[^}]*min-width:\s*32px/,
  "bkm-close 必须带 32px 最小尺寸"
);

// ---- ⑥ 第四轮：统一编辑工作台（question-editor.css + library-unified-editor.js）窄屏堆叠与聚焦保护 ----

assert.ok(!html.includes('id="editQuestionModal"'), "旧独立改题弹窗 #editQuestionModal 应已彻底移除");
assert.ok(!html.includes('id="solutionEditorModal"'), "旧独立解答弹窗 #solutionEditorModal 应已彻底移除");

const editorCss = fs.readFileSync(path.join(__dirname, "question-editor.css"), "utf8");
const editorMediaBlocks = extractMediaBlocks(editorCss);
const editor1100 = editorMediaBlocks
  .filter((b) => b.condition.includes("max-width: 1100px"))
  .map((b) => b.body)
  .join("\n");
const editor960 = editorMediaBlocks
  .filter((b) => b.condition.includes("max-width: 960px"))
  .map((b) => b.body)
  .join("\n");

assert.match(
  editor960,
  /\.editor-bar\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/,
  "统一工作台顶栏在窄屏应两行折行不溢出"
);
assert.match(
  editor1100,
  /dialog\.qb-editor-shell\[open\][\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)[\s\S]*overflow-y:\s*auto/,
  "统一工作台三区布局在窄屏应改为单栏竖向堆叠"
);

const unifiedJs = fs.readFileSync(path.join(__dirname, "library-unified-editor.js"), "utf8");
assert.match(
  unifiedJs,
  /target\.focus\?\.\(\{\s*preventScroll:\s*true\s*\}\)/,
  "统一工作台打开聚焦目标字段时必须 preventScroll，防止表单区顶出视野"
);
assert.match(
  unifiedJs,
  /controls\.stemInput\.focus\?\.\(\{\s*preventScroll:\s*true\s*\}\)/,
  "统一工作台默认聚焦题干框时必须 preventScroll"
);

// ---- ⑦ 第五轮：A4 试卷预览弹窗窄屏下的截断 --------------------------------
// 390×844 实测：底部说明文字被 truncate 削剩「预览与导…」，徽章被削成
// 「A4 · 共 1…」，两处都是「有信息但看不到」。

assertInMedia(
  "max-width: 640px",
  /#basketPreviewModal\s+#previewStatusLine\s*\{[^}]*white-space:\s*normal/,
  "A4 预览弹窗底部说明文字窄屏不得被 truncate 削掉"
);
assertInMedia(
  "max-width: 640px",
  /#basketPreviewModal\s+#previewStatusBadge\s*\{[^}]*white-space:\s*normal/,
  "A4 预览弹窗页数徽章窄屏不得被 truncate 削掉"
);

// ---- ⑧ 第六轮：左栏抽屉展开态的小触摸目标 --------------------------------
//
// 390px 实测（窄屏下左栏是 position:fixed 的浮层，展开后盖住中栏）：
//   · 文件夹树分组标题行的三个「+」按钮是 w-4 h-4 = 16×16 —— 手机上根本点不准。
// → 窄屏抬到 32px（宽屏零变化）。
// 侧栏底栏「展开标签地图 / 管理」已撤，标签管理只走 ≡ 主菜单，不再测底栏触摸目标。
//
// 注意：Tailwind 是本地预编译产物（/eagle/tailwind.css），
// 新增 utility class 不会生效，只能靠 class 钩子写自定义 CSS 规则 ——
// 所以 .eagle-tree-action-btn 这个钩子必须同时存在于 HTML 和窄屏规则里。

assertInMedia(
  "max-width: 640px",
  /#folderTreeContainer\s+\.eagle-tree-action-btn\s*\{[^}]*min-width:\s*32px[^}]*min-height:\s*32px/,
  "文件夹树「+」按钮窄屏抬到 32×32"
);

// HTML 里必须有这个 class 钩子，否则 CSS 定位不到
assert.match(
  html,
  /class="[^"]*\beagle-tree-action-btn\b[^"]*"/,
  "文件夹树「+」按钮缺少 class 钩子 eagle-tree-action-btn"
);

// 宽屏不得有残留：该选择器只允许出现在窄屏媒体查询里
assert.ok(
  !wideRules.includes(".eagle-tree-action-btn"),
  ".eagle-tree-action-btn 只允许出现在窄屏媒体查询里，宽屏不得有残留规则"
);
assert.doesNotMatch(
  html,
  /title="在经典题库页打开标签地图（浏览不写库）"/,
  "侧栏不得再残留标签地图底栏按钮"
);
assert.doesNotMatch(
  html,
  /title="打开标签管理中心"/,
  "侧栏不得再残留「管理」底栏按钮"
);

console.log(
  `Eagle 窄屏浮层触摸目标静态检查：OK（${htmlPath}）`
);
