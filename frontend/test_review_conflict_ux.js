"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const App = require("./app.js");
const Render = require("./qb-render.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

const finalStem = String.raw`如图，把直截面半径为 $25$ cm 的圆柱形木头锯成矩形木料。`;
const onlyExtra = `${finalStem}\n\n(1) [?]\n(2) [?]`;
const question = {
  state: "yellow", edited: false, stem: finalStem, options: {},
  reads: {
    a: { stem: finalStem, options: {} },
    b: { stem: onlyExtra, options: {} },
    c: { stem: finalStem, options: {} },
  }
};
const extra = App.analyze(question, Render);
assert.deepEqual(extra.marks, {});
assert.equal(extra.hasVisibleMarks, false);
assert.equal(extra.observedOnly.length, 1);
assert.match(extra.observedOnly[0].text, /\(1\) \[\?\]/);

const changed = App.analyze({
  ...question,
  stem: "求 x=3。",
  reads: { a: { stem: "求 x=2。", options: {} }, b: { stem: "求 x=3。", options: {} } }
}, Render);
assert.equal(changed.hasVisibleMarks, true);
assert.ok(changed.marks.stem.length > 0);

const formatOnly = App.analyze({
  ...question,
  stem: String.raw`半径为 $25$ cm。`,
  reads: {
    a: { stem: String.raw`半径为 25 cm。`, options: {} },
    b: { stem: String.raw`半径为 $25\text{ cm}$。`, options: {} },
  }
}, Render);
assert.equal(formatOnly.hasContentDifference, false);
assert.deepEqual(formatOnly.observedOnly, []);

// 长书不再把几百个页码按钮铺在图片上方。
for (const id of ["pagePrevious", "pageNumberInput", "pageNext", "pageTabs", "allPagesPicker", "pageSearchInput", "pageSearchResults"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}
assert.match(js, /function relatedDialogPages/);
assert.match(js, /function renderPageSearchResults/);
assert.doesNotMatch(js, /state\.paper\.pages\.forEach\(\(page, index\) => \{[\s\S]{0,500}tabs\.append\(tab\)/);
assert.match(css, /\.page-stage\s*\{[^}]*flex:\s*1 1 auto[^}]*min-height:\s*220px/s);
assert.match(css, /\.page-picker-popover\s*\{[^}]*position:\s*absolute[^}]*max-height:/s);

// 未分类候选显示数量并分流；快捷确认保留 A-D 归属，不再一律写成题干。
assert.match(js, /candidate_unclassified/);
assert.match(js, /检查 \$\{count\} 张候选图/);
assert.match(js, /当前配图正确，其余 \$\{count\} 张无关/);
assert.match(js, /slot: figure\.slot \|\| "stem"/);
assert.match(js, /ignoreRemaining/);

// 另一读法多出的一个“、”要带上前后文才看得懂在哪里。
const punct = App.analyze({
  state: "yellow", edited: false, stem: "其从正面看从左面看得到的形状图", options: {},
  reads: {
    a: { stem: "其从正面看、从左面看得到的形状图", options: {} },
    b: { stem: "其从正面看和从左面看得到的形状图", options: {} },
  }
}, Render);
const comma = punct.observedOnly.find((item) => item.text === "、");
assert.ok(comma, JSON.stringify(punct.observedOnly));
assert.match(comma.before, /正面看$/);
assert.match(comma.after, /^从左面看/);
assert.match(js, /reading-difference-extra/);
// 配图面板已经说明“没有图像提示词”，下面的提示列表不再重复一遍。
assert.match(js, /没有发现图像提示词\/\.test/);
console.log("review conflict UX regression checks: OK");
