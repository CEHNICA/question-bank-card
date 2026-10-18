"use strict";
// 题库页「更多筛选」面板的稳定性（1.25.24）。
//
// 老师的原话：「这个界面啊不够稳定啊，我选择了两三个标签之后，这个界面就变了，
// 格局就变了。」附的截图上题型、谁审核的、有没有答案的数字全变成 0，
// 而「谁审核的」那张卡片是凭空冒出来的 —— 它把下面所有卡片往下顶了两行。
//
// 根因有两层，缺一不可，少修一层都会重犯：
//   · 后端每一维的数字都是「换到这个维度后还剩多少题」，所以筛选一变数字就变；
//   · 界面用 emptied 兜底「别把这里本来还有什么一起收走」，于是筛到 0 时把
//     本来在题库里就不存在的那一维也顶了出来。
//
// 修法：卡片出不出现只认 presence —— 题库里到底有没有 AI 审核的题、有没有缺答案的题、
// 有没有打上正式标签的题。改筛选动不了它。
const assert = require("node:assert/strict");
const fs = require("node:fs");

const js = fs.readFileSync(require.resolve("./library.js"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");

const notEqual = (haystack, needle, message) =>
  assert.notEqual(haystack.indexOf(needle), -1, message);

// ── 一、后端得把这个数算出来，而且不能受筛选影响 ──
assert.match(js, /state\.presence = body\.presence \|\| state\.presence/,
  "接口返回的 presence 被存下来");
assert.match(js, /if \(body\.presence\) state\.presence = body\.presence;/,
  "局部刷新回来的 presence 也得跟上，不能只在首屏取一次");
assert.match(js, /presence: \{ review_ai: 0, answer_missing: 0, tags: 0 \}/,
  "初始值要先有个确定的形状，别让它是 undefined");

// ── 二、三张卡片都改看 presence ──
// 每一条都要单独钉。原来是 "当前筛选下还剩几道" 加 emptied 兜底：
//   box.hidden = !emptied && !ai && !state.review;      // 谁审核的
//   box.hidden = !emptied && !no  && !state.answer;      // 有没有答案
//   ui.tagBox.hidden = !(tags.length || ...);           // 标签
// 这三个函数在文件里并不相邻（renderTagFilter 在 renderReviewFilters 前面），
// 所以按"从它自己开始、到下一个同缩进的 function 为止"来切，别手写边界。
const sliceFn = (name) => {
  const start = js.indexOf(`\n  function ${name}(`);
  assert.notEqual(start, -1, `找得到 ${name}；改版时 update this slice`);
  const rest = js.slice(start + 1);
  const next = rest.slice(1).search(/\n {2}(async )?function \w+\(/);
  return next === -1 ? rest : rest.slice(0, next + 1);
};
const review = sliceFn("renderReviewFilters");
const answer = sliceFn("renderAnswerFilters");
const tagFilter = sliceFn("renderTagFilter");

assert.match(review, /box\.hidden = !Number\(state\.presence\?\.review_ai\) && !state\.review;/,
  "「谁审核的」看题库里有没有 AI 审核的题，不看当前筛选下还剩几道");
assert.match(answer, /box\.hidden = !Number\(state\.presence\?\.answer_missing\) && !state\.answer;/,
  "「有没有答案」看题库里有没有缺答案的题，不看当前筛选下还剩几道");
assert.match(tagFilter, /ui\.tagBox\.hidden = !\(tags\.length \|\| Number\(state\.presence\?\.tags\)/,
  "「标签」看题库里有没有打过标签的题，不只看当前筛出来的几个");

// emptied 这条兜底必须整条消失：它正是把"题库里根本没有的那一维"顶出来的元凶。
assert.doesNotMatch(js, /const emptied = /,
  "emptied 这条兜底已经整条拿掉 —— 它会把题库里本来不存在的那一维也顶出来");
for (const fn of [review, answer, tagFilter]) {
  assert.doesNotMatch(fn, /emptied/, "卡片出不出现的判断里不能再有 emptied");
}

// ── 三、当前正选着的那一维，永远得留着 ──
// 反过来也要钉：筛着筛着把自己的控件筛没了，那是另一种"界面变了"。
assert.match(review, /&& !state\.review;/, "正选着「谁审核的」时，那张卡片不许消失");
assert.match(answer, /&& !state\.answer;/, "正选着「有没有答案」时，那张卡片不许消失");
assert.match(tagFilter, /\|\| state\.tagAll \|\| state\.tagAny \|\| state\.tagNot \|\| state\.tag\)/,
  "正选着标签时，「标签」那张卡片不许消失");

// ── 四、筛不到标签时别留一个空盒子 ──
// 卡片留下是为了格局稳定，不是因为该留一个空的多选框。写一句人话说明。
assert.match(tagFilter, /if \(!tags\.length\) ui\.tagSelect\.append\(new Option\("当前筛选下没有可用的标签", ""\)\)/,
  "当前筛不到标签时，多选框里写清是筛选的问题，不是没有标签这回事");

// ── 五、布局本身也不能因为少一张卡片就重排 ──
// 上面的规则保证卡片集合不再变化；这条保证就算集合真的变了（比如题库是空的），
// 剩下的卡片也不会在行与行之间乱跳。
assert.match(css, /\.library-advanced\[open\] > \.library-filters \{[^}]*display: grid; grid-template-columns: repeat\(auto-fit, minmax\(292px, 1fr\)\)/,
  "面板是等宽栅格，每张卡占同样宽的一格");
assert.match(css, /\.library-advanced\[open\] > \.library-filters > \.library-facet,\s*\n\s*\.library-advanced\[open\] > \.library-filters > \.tag-saved-filter-box \{ grid-column: 1 \/ -1; \}/,
  "题型和常用筛选永远占满整行，不跟着别的卡片跑");

console.log("The More Filters panel keeps its shape: which cards exist depends on the library, "
  + "not on what the current filter happens to match: OK");
