"use strict";
// 「更多筛选」面板重排（1.25.18）。实机截图指出来的：题型那排标签的最后一个
// 「解答题 629」右边紧贴着「排序」两个字，排序的下拉框又孤零零飘到下一行右端；
// 有没有答案、标签、标签关系、常用筛选各占各的列，没有一条统一的竖线；标签那个
// 多选框还是系统灰列表框。量出来七块的高度是 30 / 61 / 35 / 105 / 61 / 96 ——
// 没有任何节奏。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const markup = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");

const panel = markup.slice(markup.indexOf('<details id="advancedFilters"'),
                           markup.indexOf('<div id="tagQuickTools"'));
assert.ok(panel.length > 0, "找得到「更多筛选」那块");

// ── 一、面板是一张卡片一张卡片排的，不是一排 flex 换行挤在一起的 ──
const openPanel = css.match(/\.library-advanced\[open\] > \.library-filters \{([^}]*)\}/);
assert.ok(openPanel, "找得到面板本身的规则");
assert.match(openPanel[1], /display:\s*grid/, "面板排成栅格");
assert.match(openPanel[1], /grid-template-columns:\s*repeat\(auto-fit/, "按宽度自动分列");
assert.doesNotMatch(openPanel[1], /flex-wrap:\s*wrap/,
  "不能再靠 flex 换行挤 —— 那样三列对不齐，那正是这次要修的毛病");

// ── 二、每一张卡片自己带标题，控件在标题下面 ──
const card = css.match(/\.library-advanced\[open\] > \.library-filters > \* \{([^}]*)\}/);
assert.ok(card, "每张卡片都要有样式");
assert.match(card[1], /flex-direction:\s*column/, "标题在上、控件在下");
assert.match(card[1], /border:[^;]*var\(--line\)/, "卡片要有边，看起来才是一个个筛选项");
assert.match(card[1], /border-radius/, "卡片要有圆角");

// 题型最宽，独占一行；常用筛选带两颗按钮，也独占一行。
const full = css.match(/\.library-advanced\[open\] > \.library-filters > \.library-facet,\s*\n\s*\.library-advanced\[open\] > \.library-filters > \.tag-saved-filter-box \{([^}]*)\}/);
assert.ok(full, "题型和常用筛选要独占一行");
assert.match(full[1], /grid-column:\s*1 \/ -1/);

// ── 三、题型原来是横着的（标签和按钮一行），这里要跟别的卡片一致竖着 ──
const facet = css.match(/\.library-advanced\[open\] > \.library-filters \.library-facet,\s*\n\s*\.library-advanced\[open\] > \.library-filters \.library-filter-group \{([^}]*)\}/);
assert.ok(facet, "题型和两个补上标题的维度要有同一套排法");
assert.match(facet[1], /flex-direction:\s*column/, "题型也得改成标题在上");

// ── 四、两个只有读屏名字的维度，屏幕上得有标题 ──
const groupLabel = markup.match(/<span class="library-filter-group-label">([^<]+)<\/span>\s*\r?\n\s*<div id="(reviewFilters|answerFilters)"/);
assert.ok(groupLabel, "谁审核的 / 有没有答案 得有屏幕上的标题");
for (const id of ["reviewFilters", "answerFilters"]) {
  assert.match(markup, new RegExp(`id="${id}"[^>]*role="group"`), `${id} 仍要留着读屏用的名字`);
}
assert.match(markup, /library-filter-group-label">有没有答案</);
assert.match(markup, /library-filter-group-label">谁审核的</);

// 标题跟着控件一起消失：这个维度没数据时不留空壳。
const hide = css.match(/\.library-advanced\[open\] > \.library-filters > \.library-filter-group:has\(\[hidden\]\) \{([^}]*)\}/);
assert.ok(hide, "空维度要整块藏掉");
assert.match(hide[1], /display:\s*none/);
// 少写一层 `.library-filters` 就整条失效 —— 实机才发现「谁审核的」空卡片还在。
assert.doesNotMatch(css, /\.library-advanced\[open\] > \.library-filter-group:has/,
  "选择器里不能少 `.library-filters` 这一层：卡片是它的子元素，不是 <details> 的直接子元素");

// ── 五、标签那个多选框得撑满卡片，否则还是系统灰列表框的样子 ──
const tagSelect = css.match(/\.library-advanced\[open\] > \.library-filters #tagSelect \{([^}]*)\}/);
assert.ok(tagSelect, "多选框有专门样式");
assert.match(tagSelect[1], /width:\s*100%/);
assert.match(tagSelect[1], /border-radius/);

// 窄屏规则里那条把下拉标题全藏掉的，不能又把「更多筛选」里的一起藏了（1.25.17 修过）。
assert.doesNotMatch(css, /\.library-filterbar \.library-select span \{ display: none; \}/,
  "后代选择器会把面板里那两个下拉的标题也藏掉");

console.log("The 更多筛选 panel lays out as aligned titled cards, not a flex-wrap pile: OK");
