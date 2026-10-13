"use strict";
// 几何证明在纸上是一步一行：∵ … / ∴ … / 又 ∵ …。
// 这些符号被判成公式段，它们前面那一次换行又被 tidyText 当成软换行吃掉，
// 于是每一步推理都被并进上一行，整段证明压成流水账。
// CARD_781 / CARD_802 是真库里《数学八年级下册》的两张卡，题面逐字照抄。
// 断言的是「印出来的每一行」，不是某个元素的个数：题面里以 ∵ / ∴ / 又 ∵ 开头的
// 行有几行，纸面上就得有几行；连接词不许吊在上一行末尾。
const assert = require("node:assert/strict");
const QB = require("./qb-render.js");

const CARD_781 = "根据菱形的定义，邻边相等的平行四边形是菱形。除此之外，你认为还有什么条件可以判断一个平行四边形是菱形？先想一想，再与同伴交流。\n\n平行四边形的不少性质定理与判定定理都是互逆命题。受此启发，我猜想：四边相等的平行四边形是菱形，对角线互相垂直的平行四边形是菱形。\n\n我觉得，对角线互相垂直的平行四边形有可能是菱形。但“四边相等的平行四边形是菱形”嘛……实际上与“邻边相等的平行四边形是菱形”一样。\n\n你是怎么想的？你认为小明的想法如何？\n\n已知：如图 6-3，在 ▱ABCD 中，对角线 AC 与 BD 相交于点 O，AC ⊥ BD。\n求证：▱ABCD 是菱形。\n证明：∵ 四边形 ABCD 是平行四边形，\n∴ OA = OC。\n又 ∵ AC ⊥ BD，\n∴ BD 所在的直线是线段 AC 的垂直平分线。\n∴ BA = BC。\n∴ 四边形 ABCD 是菱形（菱形的定义）。\n\n定理 对角线互相垂直的平行四边形是菱形。\n\n议一议\n已知线段 AC，你能用尺规作图的方法作一个菱形 ABCD，使 AC 为菱形的一条对角线吗？";
const CARD_802 = "做一做\n\n如图 6-16，在一个平行四边形活动框架上，用两根橡皮筋分别套在相对的两个顶点上，拉动一对不相邻的顶点时，平行四边形的形状会发生变化。\n\n（1）随着 $\\angle \\alpha$ 的变化，两条对角线的长度将发生怎样的变化？\n（2）当两条对角线的长度相等时，平行四边形有什么特征？由此你能得到一个怎样的猜想？\n\n定理 对角线相等的平行四边形是矩形。\n\n已知：如图 6-17，在 ▱ABCD 中，AC，DB 是它的两条对角线，AC=DB。\n求证：▱ABCD 是矩形。\n\n证明：∵ 四边形 ABCD 是平行四边形，\n∴ AB=DC，AB//DC。\n又 ∵ BC=CB，AC=DB，\n∴ △ABC ≌ △DCB。\n∴ ∠ABC=∠DCB。\n∵ AB//DC，\n∴ ∠ABC+∠DCB=180°。\n∴ ∠ABC=∠DCB=90°。\n∴ ▱ABCD 是矩形（矩形的定义）。\n\n想一想\n\n我们知道，矩形的四个角都是直角。反过来，一个四边形至少有几个角是直角时，这个四边形就是矩形呢？能证明你的结论吗？与同伴交流。\n\n定理 有三个角是直角的四边形是矩形。\n\n议一议\n\n你有什么方法检查你家（或教室）刚安装的门框是不是矩形？如果仅有一根较长的绳子，你怎样检查？请说明检查方法的合理性，并与同伴交流。";

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.style = {}; this.className = "";
    this.nodeValue = "";
    this.classList = { add: name => { this.className = `${this.className} ${name}`.trim(); } };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll() { return []; }
}
global.document = { createElement: tag => new Element(tag), createTextNode: text => ({ nodeType: 3, nodeValue: text }) };
global.katex = { render: (latex, node) => { node.innerHTML = `katex:${latex}`; } };

const REASON_LINE = /^(?:又\s*)?[\u2235\u2234]/;
const descend = (node, out = []) => { for (const child of node.children || []) { out.push(child); descend(child, out); } return out; };

function render(source) {
  const host = document.createElement("div");
  QB.renderTypeset(host, source);
  return host;
}

/*
 * 纸面上真正看到的行：`<br>` 是断行，文字节点里的换行也是断行（题干
 * 排版是 pre-wrap），公式段按它自己的原样算一个不可拆的原子。
 */
function printedStream(host) {
  return (host.children || []).map(child => {
    if (child.tagName === "BR") return "\n";
    // 公式按它自己的原样算；平行四边形之类的符号按渲染出来的字符算。
    if (child.className?.split(" ").includes("qb-math")) return child.dataset.raw;
    if (child.children?.length) return printedStream(child);
    return child.tagName ? (child.textContent ?? "") : (child.nodeValue ?? "");
  }).join("");
}
const printedLines = host => printedStream(host).split("\n").map(line => line.trim()).filter(line => line !== "");
const squeeze = value => value.replace(/\s+/g, "");

function assertProofReadsAsSteps(source, label) {
  const host = render(source);
  const wanted = source.split("\n").filter(line => REASON_LINE.test(line.trim()));
  const lines = printedLines(host);
  const printed = lines.filter(line => REASON_LINE.test(line));
  assert.equal(printed.length, wanted.length,
    `${label}: 题面有 ${wanted.length} 行以 ∵/∴ 开头，纸面上应各占一行，实得\n  ${JSON.stringify(lines, null, 1)}`);
  // 「又」领着下一步，它不能吊在上一行末尾。
  lines.forEach((line, index) => {
    assert.doesNotMatch(line, /[又而则]\s*$/, `${label}: 第 ${index + 1} 行以连接词结尾，实得 ${JSON.stringify(line)}`);
  });
  // 补进去的只能是换行：纸上仍是题面那些字，一个不多一个不少。
  assert.equal(squeeze(printedStream(host)), squeeze(source), `${label}: 断行不得增删或改动任何一个字`);
  return host;
}

assertProofReadsAsSteps(CARD_781, "卡 781");
assertProofReadsAsSteps(CARD_802, "卡 802");

// 证明：后的第一个 ∵ 跟在冒号后面，本来就同一行，不该再多断一次。
assert.equal(printedLines(render("证明：∵ 四边形 ABCD 是平行四边形，\n∴ OA = OC。")).length, 2);
// 上一行没有句号收尾时，那是识读稿的软换行，不能顺手把卷子拆散。
assert.equal(printedLines(render("如图 6-3，\nAC ⊥ BD。\n△ABC ≌ △DCB。")).length, 1);
assert.equal(printedLines(render("请在图中找出两对相似\n而不全等的三角形。")).length, 1);
// 上一行以句号收尾、下一行以「又」领头时，那是真的另起一段。
assert.deepEqual(printedLines(render("∴ OA = OC。\n又 ∵ AC ⊥ BD，")).map(squeeze), ["∴OA=OC。", "又∵AC⊥BD，"]);
// 独立成行的公式块后面的推理符号：换行必须落在外层，不能插进公式块里。
{
  const host = render("$$\\text{▱}ABCD$$\n∴ x = 1");
  const groups = (host.children || []).filter(child => child.className === "qb-math-display-group");
  assert.equal(groups.length, 1, "夹具确实含一个独立成行的公式块");
  assert.equal(descend(groups[0]).filter(child => child.tagName === "BR").length, 0,
    "换行不能插进公式块内部");
  assert.equal(printedLines(host).length, 2);
}
console.log("geometric proof steps: every ∵ / ∴ / 又 ∵ line starts its own line, and nothing else is broken: OK");