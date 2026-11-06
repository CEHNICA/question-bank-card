"use strict";

// 1.26.41：批量通过的结果面板。
//
// 一卷几百道题，逐条平铺会把老师淹没。这里钉住三件事：
//   1) 过不去的题按原因归类，多的排前面；
//   2) 每类折叠起来，展开才列出是哪几道，定位要能对回原书；
//   3) 页面上的结果面板真的调用了这个模块（不是只测模块本身）。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const R = require("./approve-result.js");

// ── 一套够用的假 DOM ────────────────────────────────────────
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.className = "";
    this._text = "";
  }
  append(...kids) {
    for (const kid of kids) {
      this.children.push(kid);
      if (kid && typeof kid === "object") kid.parent = this;
    }
  }
  replaceChildren(...kids) {
    this.children = [];
    this.append(...kids);
  }
  get textContent() {
    return this._text + this.children.map((kid) => (typeof kid === "string" ? kid : (kid.textContent || ""))).join("");
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
}

globalThis.document = { createElement: (tag) => new El(tag) };

function walk(node, out = []) {
  for (const kid of node.children || []) {
    if (!kid || typeof kid !== "object" || !kid.children) continue;
    out.push(kid);
    walk(kid, out);
  }
  return out;
}

function byClass(root, className) {
  return walk(root).filter((node) => String(node.className || "").split(" ").includes(className));
}

// ── 一、归类 ────────────────────────────────────────────────────
{
  const groups = R.groupByReason([
    { number: 1, category: "题型还没定" },
    { number: 2, category: "按章节结构重新提取" },
    { number: 3, category: "按章节结构重新提取" },
    { number: 4, category: "按章节结构重新提取" },
  ]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].reason, "按章节结构重新提取", "多的排前面");
  assert.equal(groups[0].items.length, 3);
  assert.equal(groups[1].reason, "题型还没定");
}

// 没有类别时退回第一条提醒，再没有就用原因本身。
assert.equal(R.categoryOf({ flags: ["第一条提醒", "第二条"] }), "第一条提醒");
assert.equal(R.categoryOf({ reason: "还没读出题干" }), "还没读出题干");
assert.equal(R.categoryOf({}), "其它原因");

// ── 二、渲染 ────────────────────────────────────────────────────
const SKIPPED = [
  {
    number: 1,
    category: "按章节结构重新提取，请对照新原图范围核对",
    detail: "",
    group_title: "章节01 · 公式法",
    section: "第1节 公式法 · 例题1",
    stem: "已知 $\\tan\\alpha=\\frac{1}{2}$",
    page: 128,
  },
  {
    number: 2,
    category: "按章节结构重新提取，请对照新原图范围核对",
    detail: "原审核记录保留在旧版本中",
    group_title: "章节01 · 公式法",
    section: "第1节 公式法 · 例题2",
    stem: "设 $\\{a_n\\}$ 是等比数列",
    page: 129,
  },
  {
    number: 3,
    category: "题型还没定",
    detail: "请先在题号旁边选一下题型",
    group_title: "章节02 · 配方法",
    section: "第2节 配方法 · 例题1",
    stem: "解方程：$x^2-2x+1=0$",
    page: 140,
  },
  {
    number: 7,
    category: "还没读出题干",
    detail: "请先改字或重新识读",
    group_title: "",
    section: "",
    stem: "",
    page: 151,
  },
];

{
  const container = new El("div");
  const result = R.render(container, { approved: 0, problems: [], skipped: SKIPPED });

  assert.equal(result.approved, 0);
  assert.equal(result.skipped, 4);
  assert.equal(result.groups.length, 3, "四种原因里前三种各归一类");

  const text = container.textContent;
  assert.ok(text.includes("已入库 0 道"), "先说入库了几道");
  assert.ok(text.includes("还有 4 道没能入库，分成 3 类原因"), "再说过不去的分成几类");

  // 一道都没过时，要说清卡在哪儿
  assert.ok(
    text.includes("这一卷暂时没有题能直接入库。最多的一类是 2 道，卡在同一件事上：按章节结构重新提取，请对照新原图范围核对。"),
    "一道都没过时要把最大的一类说清楚"
  );

  // 分组标题
  const heads = byClass(container, "approve-result-group-head").map((node) => node.textContent);
  assert.deepEqual(heads, [
    "按章节结构重新提取，请对照新原图范围核对（2 道）",
    "题型还没定（1 道）",
    "还没读出题干（1 道）",
  ]);

  // 每类折叠起来，展开才列出是哪几道
  assert.equal(byClass(container, "approve-result-group").length, 3);
  assert.equal(byClass(container, "approve-result-items").length, 3);

  // 定位要能对回原书
  const wheres = byClass(container, "approve-result-where").map((node) => node.textContent);
  assert.deepEqual(wheres, [
    "第 128 页 · 章节01 · 公式法 · 第 1 题",
    "第 129 页 · 章节01 · 公式法 · 第 2 题",
    "第 140 页 · 章节02 · 配方法 · 第 3 题",
    "第 151 页 · 第 7 题",
  ]);

  // 题干开头也要带上来
  const stems = byClass(container, "approve-result-stem").map((node) => node.textContent);
  assert.deepEqual(stems, [
    "已知 $\\tan\\alpha=\\frac{1}{2}$",
    "设 $\\{a_n\\}$ 是等比数列",
    "解方程：$x^2-2x+1=0$",
  ]);

  // 分组标题已经说了卡在哪一类，每行只补自己多出来的那一条
  const details = byClass(container, "approve-result-detail").map((node) => node.textContent);
  assert.deepEqual(details, ["原审核记录保留在旧版本中", "请先在题号旁边选一下题型", "请先改字或重新识读"]);
}

// 原因本身是一整句时，句尾不能再多一个句号（面板上会印成「。。」）。
{
  const container = new El("div");
  R.render(container, {
    approved: 0,
    problems: [],
    skipped: [
      {
        number: 1,
        category: "按章节结构重新提取，请对照新原图范围核对。",
        detail: "",
        group_title: "章节01 · 公式法",
        section: "第1节 公式法 · 例题1",
        stem: "已知",
        page: 3,
      },
    ],
  });
  const text = container.textContent;
  assert.ok(!text.includes("。。"), "句尾不能出现两个句号");
  assert.ok(
    text.includes("卡在同一件事上：按章节结构重新提取，请对照新原图范围核对。"),
    "最多的一类后面仍然要补上句号"
  );
  const heads = byClass(container, "approve-result-group-head").map((node) => node.textContent);
  assert.deepEqual(heads, ["按章节结构重新提取，请对照新原图范围核对（1 道）"], "分组标题里不留多余句号");
}

// 全部入库时不说多余的话。
{
  const container = new El("div");
  R.render(container, { approved: 12, problems: [], skipped: [] });
  assert.ok(container.textContent.includes("已入库 12 道"));
  assert.ok(container.textContent.includes("这一卷剩下的题都已经入库了。"));
  assert.equal(byClass(container, "approve-result-group").length, 0, "没有过不去的题就不该有分组");
}

// 保存没完成的题要单独报，不能混在「过不去」里。
{
  const container = new El("div");
  R.render(container, { approved: 1, problems: ["第 2 题保存没完成"], skipped: [] });
  assert.ok(container.textContent.includes("另有 1 道保存未完成，请重试"));
  const problems = byClass(container, "approve-result-problem").map((node) => node.textContent);
  assert.deepEqual(problems, ["第 2 题保存没完成"]);
}

// 从源文件里原样抠出一个函数（先配对圆括号，再配对大括号）。
function sliceFunction(source, name) {
  const start = source.indexOf(`\n  function ${name}(`);
  assert.notEqual(start, -1, `源文件里找不到 ${name}`);
  const parenStart = source.indexOf("(", start);
  let depth = 0;
  let i = parenStart;
  for (; i < source.length; i += 1) {
    if (source[i] === "(") depth += 1;
    else if (source[i] === ")") {
      depth -= 1;
      if (!depth) break;
    }
  }
  const bodyStart = source.indexOf("{", i);
  depth = 0;
  for (i = bodyStart; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (!depth) break;
    }
  }
  return source.slice(start + 1, i + 1);
}

// ── 三、页面上的结果面板真的调了这个模块 ───────────────────────
{
  const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  const code = sliceFunction(appJs, "showApproveResult");

  const container = new El("div");
  const dialog = { open: false, showModal() { this.open = true; } };
  const sandbox = {
    window: { QBApproveResult: R },
    el: (tag, className, text) => {
      const node = new El(tag);
      node.className = className || "";
      if (text != null) node.textContent = text;
      return node;
    },
    $: (id) => (id === "approveResultList" ? container : id === "approveResultDialog" ? dialog : null),
  };
  vm.runInNewContext(`${code}\nglobalThis.__run = showApproveResult;`, sandbox);
  sandbox.__run({ approved: 0, problems: [], skipped: SKIPPED });

  assert.ok(dialog.open, "结果面板要弹出来");
  assert.ok(container.textContent.includes("还有 4 道没能入库，分成 3 类原因"), "弹出来的真是这个模块画的面板");
  assert.ok(
    container.textContent.includes("第 128 页 · 章节01 · 公式法 · 第 1 题"),
    "弹出来的面板要带定位"
  );
  assert.equal(byClass(container, "approve-result-group").length, 3, "弹出来的面板是分组折叠的");
}

// ── 四、模块没加载出来时要说实话 ───────────────────────────────
{
  const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  const code = sliceFunction(appJs, "showApproveResult");

  const container = new El("div");
  const dialog = { open: false, showModal() { this.open = true; } };
  const sandbox = {
    window: {},
    el: (tag, className, text) => {
      const node = new El(tag);
      node.className = className || "";
      if (text != null) node.textContent = text;
      return node;
    },
    $: (id) => (id === "approveResultList" ? container : id === "approveResultDialog" ? dialog : null),
  };
  vm.runInNewContext(`${code}\nglobalThis.__run = showApproveResult;`, sandbox);
  sandbox.__run({ approved: 0, problems: [], skipped: SKIPPED });

  assert.ok(container.textContent.includes("没加载出来"), "模块没加载出来要说实话，不能装作一切正常");
  assert.ok(!container.textContent.includes("已入库 0 道"), "没渲染出来就不许报入库几道");
}

console.log("approve result: ok");
