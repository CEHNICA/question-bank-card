"use strict";
// 题库页第二遍精简（1.25.19）。两件事，都是在实机上量出来的，不是看代码想出来的：
//   · 右侧面板把左边题卡又抄了一遍 —— 题号、题型、试卷名，一个折叠的「查看当前题面」
//     还把整道题连选项带公式渲染了第二份。同一屏里试卷名出现了 17 次。
//     「当前选中 / 定位题卡」更是在说「这里讲的就是左边那道题」，而那道题本来就
//     绿框标着，伸手就能点。用户选了更彻底那版：面板只留真正能做的事。
//   · 同一张卷连着几十道题，试卷名每张卡都写一遍。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const js = fs.readFileSync(require.resolve("./library.js"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");
const readingCss = fs.readFileSync(require.resolve("./library-reading.css"), "utf8");

// ── 一、面板里不该再有的东西 ──
// 只查整份文件不够：别处（比如别的页面的同名类）出现一下照样过关。量的必须是
// renderInspector 这一个函数体 —— 面板是它画出来的。
const panel = js.slice(js.indexOf("  function renderInspector("), js.indexOf("  function markInspectorToggle("));
assert.notEqual(panel.indexOf("function renderInspector("), -1, "renderInspector was renamed or moved; update this slice");

for (const [gone, why] of [
  ["question-inspector-title", "题号和题型左边题卡上已经有了"],
  ["question-inspector-source", "试卷名现在只在题卡上出现一次"],
  ["question-inspector-target", "「当前选中 / 定位题卡」是在重复一件眼前就看得见的事"],
  ["question-inspector-locate", "面板里不再有这个按钮"],
  ["question-inspector-preview", "「查看当前题面」把整道题又渲染了一遍"],
]) {
  assert.doesNotMatch(panel, new RegExp(gone), `${gone} 不该再被画出来：${why}`);
}
// 这两条查的是会被画出来的那段文字本身，不是随便提到这几个字。函数里留着一条
// 「原来有定位题卡」的说明注释是应该的 —— 按词查会把注释一起判成没删干净。
assert.doesNotMatch(panel, /"定位题卡"/, "面板里不再有「定位题卡」这个入口");
assert.doesNotMatch(panel, /"查看当前题面"/, "面板里不再有「查看当前题面」这个入口");
assert.doesNotMatch(panel, /"当前选中"/, "面板里不再有「当前选中」那一行");
assert.doesNotMatch(panel, /inspectorFact\(facts, "来源"/, "「来源」这一行和题卡、面板顶部的卷名是同一句话说了三遍");

// 删掉的四样不能顺手把面板掏空：真正只在这里做的事得留着。
assert.match(panel, /extrasNode\(item\)/, "整理标签是面板留下来的理由，不能一起删掉");
assert.match(panel, /node\("section", "question-inspector-tags"\)/, "标签区留着");
assert.match(panel, /node\("details", "question-inspector-info"\)/, "折叠的「题目信息」留着：核对状态题卡上没有");
assert.match(panel, /inspectorFact\(facts, "核对"/, "核对状态得留着");

// 面板空了的时候那行提示不能再承诺已经不存在的「来源」。
assert.match(js, /先在左边点一道题，这里才有它的标签和待核对建议/,
  "空面板的提示不许再提「来源」—— 面板里已经没有这一项了");
assert.doesNotMatch(js, /当前题目的来源、标签和待核对建议/,
  "开关的悬停说明也不许再承诺面板里有来源");

// ── 二、删掉的样式要跟着走干净 ──
// 留着就是骗下一个人：他会以为面板上还有那块。
for (const dead of [
  "question-inspector-title", "question-inspector-target", "question-inspector-locate",
  "question-inspector-source", "question-inspector-preview", "question-inspector-paper",
  "inspector-focus-pulse", "locateInspectorQuestion",
]) {
  assert.doesNotMatch(css, new RegExp(dead), `${dead} 在主样式里已经匹配不到任何东西了`);
  assert.doesNotMatch(readingCss, new RegExp(dead), `${dead} 在阅读样式里也已经匹配不到任何东西了`);
}
// 标题那块原来左边挂着强调竖线，标题没了竖线也得跟着搬到标签区，
// 不然面板一下子就没了「这是在讲当前这道题」的锚点。
assert.match(css, /\.question-inspector-tags \{[^}]*border-left: 3px solid var\(--accent\)/,
  "强调竖线搬到标签区头上");

// ── 三、试卷名同一张卷只写一次 ──
// 只查「有没有这个函数」等于没查 —— 写错了判断照样通过。这里把真的那个函数
// 拿出来跑，看它到底按什么规则显示。
const syncCode = js.slice(js.indexOf("  function syncSourceLabel("), js.indexOf("  function card(item) {"));
assert.notEqual(syncCode.indexOf("function syncSourceLabel"), -1, "syncSourceLabel was renamed or moved; update this slice");
const sandbox = {};
vm.runInNewContext(syncCode, sandbox);

const cardNode = () => {
  const label = { hidden: false, remove() { this.removed = true; } };
  return { label, querySelector: (selector) => (selector === ".library-source" ? label : null) };
};

const first = cardNode();
sandbox.syncSourceLabel(first, { source_filename: "A.pdf" }, null);
assert.equal(first.label.hidden, false, "一串里的第一张要写出试卷名");

const sameRun = cardNode();
sandbox.syncSourceLabel(sameRun, { source_filename: "A.pdf" }, { source_filename: "A.pdf" });
assert.equal(sameRun.label.hidden, true, "同一张卷连着的后面几张不再重复写");

const nextPaper = cardNode();
sandbox.syncSourceLabel(nextPaper, { source_filename: "B.pdf" }, { source_filename: "A.pdf" });
assert.equal(nextPaper.label.hidden, false, "换了一张卷就得重新写出来");

// 判断只认「上一张」，不能把整页都算进去 —— 否则两张卷交替出现时会一张都不写。
const alternating = cardNode();
sandbox.syncSourceLabel(alternating, { source_filename: "A.pdf" }, { source_filename: "B.pdf" });
assert.equal(alternating.label.hidden, false, "上一张是别的卷，这一张仍然要写");

// 题卡本身还在造这个元素：没有它，上面什么也藏不了。
assert.match(js, /node\("span", "library-source", item\.source_filename\)/, "题卡上仍然要有那个试卷名的位置");

// 藏要用 hidden，不能把它从 DOM 里摘掉：题卡是复用的，上面那张被筛掉之后
// 这一张要能从「不写」变回「要写」，摘掉就再也回不来了。
assert.match(syncCode, /label\.hidden =/, "藏的方式是 hidden，不是 remove/移除节点");
const removed = cardNode();
sandbox.syncSourceLabel(removed, { source_filename: "A.pdf" }, { source_filename: "A.pdf" });
assert.equal(removed.label.removed, undefined, "不许把那个元素从 DOM 里摘掉");
assert.equal(removed.label.hidden, true, "摘掉的话这一张就再也不会自己变回「要写」");

// 两个调用点都要过一遍：一个在渲染循环里（每次都重新判），一个在展开题面整张
// 换掉之后（那一处不走渲染循环）。漏掉展开那处，展开一次卷名就冒出来一次。
assert.match(js, /shown\.forEach\(\(item, index\) =>[\s\S]*?syncSourceLabel\(node, item, index > 0 \? shown\[index - 1\] : null\)/,
  "渲染循环里按前后顺序判一次");
assert.match(js, /const replacement = card\(item\);[\s\S]*?syncSourceLabel\(replacement, item,[\s\S]*?article\.replaceWith\(replacement\)/,
  "展开题面整张换掉之后也要重新判一次");

console.log("The panel no longer repeats the card, and each paper is named once: OK");
