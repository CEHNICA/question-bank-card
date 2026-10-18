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
const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

const panel = markup.slice(markup.indexOf('<details id="advancedFilters"'),
                           markup.indexOf('</details>', markup.indexOf('<details id="advancedFilters"')));
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

// 1.25.19：上面那条只管「卡片里包着一个藏起来的维度」。卡片自己带 hidden 是另一回事，
// 而上面第二节把每个孩子都写成了 display:flex —— 作者样式压得住 [hidden] 自带的
// display:none，于是「标签」「常用筛选」在一个标签都还没有的新库里照样露出来。
const hideSelf = css.match(/\.library-advanced\[open\] > \.library-filters > \[hidden\] \{([^}]*)\}/);
assert.ok(hideSelf, "卡片自己标着先藏起来的时候也要藏住");
assert.match(hideSelf[1], /display:\s*none/);

// ── 五、标签筛选是一排勾选框，不是系统灰列表框（1.25.27）──
// 1.25.18 只把它撑满了卡片，实机上仍然是一个「点一下选中、按住 Ctrl 才能多选、
// 鼠标划过留一条深色高亮」的系统列表框。老师原话是这个界面「感觉有 bug」。
const tagSelect = css.match(/\.library-advanced\[open\] > \.library-filters #tagSelect \{([^}]*)\}/);
assert.ok(tagSelect, "标签筛选那一块有专门样式");
assert.match(tagSelect[1], /width:\s*100%/);
assert.match(tagSelect[1], /border-radius/);
assert.match(tagSelect[1], /max-height:\s*158px/, "标签多了在框里滚，不把面板撑高");
assert.doesNotMatch(markup, /<select id="tagSelect"[^>]*multiple/,
  "系统多选框已经换掉：多选靠勾选框，不靠按住 Ctrl");
assert.match(markup, /<div id="tagSelect" class="library-tag-choices" role="group"/,
  "换成一组勾选框，仍然是一个有名字的组");
assert.match(css, /\.library-tag-choice \{ display: flex;/, "每一行是「勾选框 + 标签名」");
assert.match(css, /\.library-tag-choice-box \{[^}]*accent-color/, "勾选框跟界面同色，不是系统默认蓝");
// 选中的判定必须读勾选框；还读 selectedOptions 的话，换掉控件之后筛选就永远空了。
const tagFilter = js.slice(js.indexOf("  function tagChoice("), js.indexOf("  function savedTagFilters("));
assert.match(tagFilter, /function selectedTagIds\(\)[\s\S]*?input\[type="checkbox"\]:checked/,
  "「选中了哪几个」从勾选框读");
assert.match(tagFilter, /box\.checked = Boolean\(checked\)/, "重建列表时把勾选状态放回去");
assert.match(tagFilter, /ui\.tagSelect\.scrollTop = scrollTop/, "勾一个标签后重新读题库，滚动位置要留住");
assert.doesNotMatch(js, /tagSelect\.selectedOptions/, "系统多选框的读法要清干净");

// 1.25.27：这一格原来只有「常用筛选 / 保存当前条件」几个字，没人知道它到底存什么。
// 实话是：只存上面勾的标签和「标签关系」，来源、题型、搜索词一概不存。
assert.match(markup, /tag-saved-filter-note">把勾好的标签和「标签关系」存成一条/,
  "先一句话说清它干什么");
assert.match(markup, /只记标签，不记来源、题型和搜索词/, "把它不记的东西也写明，别让人以为存了整页条件");
assert.match(markup, /记住这次勾的标签/, "按钮说它真做的事，不是含糊的「保存当前条件」");
assert.match(markup, /删除这一条/, "删除按钮说清删的是下拉框里那一条");

// 窄屏规则里那条把下拉标题全藏掉的，不能又把「更多筛选」里的一起藏了（1.25.17 修过）。
assert.doesNotMatch(css, /\.library-filterbar \.library-select span \{ display: none; \}/,
  "后代选择器会把面板里那两个下拉的标题也藏掉");

console.log("The 更多筛选 panel lays out as aligned titled cards, not a flex-wrap pile: OK");
