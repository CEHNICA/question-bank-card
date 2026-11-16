/* 第十六轮守护测试：功能入口首屏就得在。
 *
 * 缺陷：经典题库页每张卡片有个「👁 查看解析」按钮，但它是 eagle-filter-bar.js
 * 在**筛选刷新**回调里挂上去的 —— 首屏渲染出来的那批卡片没有这个按钮，
 * 滚动加载的新卡片也没有。功能在，用户点不到。
 *
 * 修法：盯着列表容器（#libraryList），一有新卡片就补上绑定。
 *
 * 尺子自带自检：把观察器从源码里抠掉 / 把调用注释掉，扫描器必须报出问题 ——
 * 否则断言恒真，「有 observer」这条永远是绿的。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = __dirname;
const FILTER_BAR = path.join(FRONTEND, "eagle-filter-bar.js");

/* ---------- 尺子 ---------- */

function extractFunction(src, signature) {
  const start = src.indexOf(signature);
  if (start < 0) return null;
  const open = src.indexOf("{", start);
  if (open < 0) return null;
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return null;
}

// 剥掉注释：只看「真正生效的代码」。
// 少了这一步，把调用注释掉（// this._observeCardList();）仍然能匹配到关键字，
// 断言就恒真了 —— 看门狗退回时正是这么干的。
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^[ \t]*\/\/.*$/gm, " ");
}

// 扫描「卡片分批渲染时，新卡片能不能也拿到入口」
function scanFirstPaint(src) {
  const problems = [];
  const observerRaw = extractFunction(src, "_observeCardList() {");
  if (!observerRaw) {
    problems.push("eagle-filter-bar.js 里没有 _observeCardList：分批渲染出来的卡片永远绑不到入口");
    return problems;
  }
  const observer = stripComments(observerRaw);
  if (!/libraryList/.test(observer)) {
    problems.push("_observeCardList 没盯住列表容器（#libraryList）");
  }
  if (!/MutationObserver/.test(observer)) {
    problems.push("_observeCardList 没有用 MutationObserver 盯容器");
  }
  if (!/_bindCardEnhancements/.test(observer)) {
    problems.push("_observeCardList 没有在新卡片出现时调用 _bindCardEnhancements");
  }

  const mountRaw = extractFunction(src, "mount(parentEl, context) {");
  if (!mountRaw) {
    problems.push("抽不到 mount()");
  } else {
    const mount = stripComments(mountRaw);
    if (!/this\._observeCardList\(\)/.test(mount)) {
      problems.push("mount() 没有启动 _observeCardList：观察器压根没开始工作");
    }
  }

  const bindRaw = extractFunction(src, "_bindCardEnhancements() {");
  if (!bindRaw) {
    problems.push("抽不到 _bindCardEnhancements()");
  } else {
    const bind = stripComments(bindRaw);
    if (!/eagleEnhanced/.test(bind)) {
      problems.push("_bindCardEnhancements 没有幂等标记（data-eagle-enhanced）：观察器会被自己的改动带着绕圈");
    }
  }
  return problems;
}

function readFilterBar() {
  return fs.readFileSync(FILTER_BAR, "utf8");
}

/* ---------- 尺子自检 ---------- */

/* 自检用的合成源码：不读工作区文件。
 * 否则看门狗往工作区打退回补丁时，自检会跟着一起红 ——
 * 一个退回点红两条，说明断言有重叠，尺子就不合格。 */
const SAMPLE_SRC = `
class EagleFilterBar {
  mount(parentEl, context) {
    this._observeCardList();
  }
  _observeCardList() {
    const list = document.getElementById("libraryList");
    const obs = new MutationObserver(() => { this._bindCardEnhancements(); });
    obs.observe(list, { childList: true, subtree: true });
  }
  _bindCardEnhancements() {
    const card = document.querySelector(".library-card");
    card.dataset.eagleEnhanced = "1";
  }
}
`;

test("尺子自检：合成源码本身必须扫干净（否则后面的变异没意义）", () => {
  assert.deepStrictEqual(scanFirstPaint(SAMPLE_SRC), []);
});

test("尺子自检：把观察器抠掉，扫描器必须报出问题（否则断言恒真）", () => {
  const broken = SAMPLE_SRC.replace(
    /\n {2}_observeCardList\(\) \{[\s\S]*?\n {2}\}\n/,
    "",
  );
  assert.notStrictEqual(broken, SAMPLE_SRC, "抠观察器的补丁没生效，用例没意义");
  const problems = scanFirstPaint(broken);
  assert.ok(problems.length >= 1, "抠掉观察器后扫描器一条都没报，断言恒真");
});

test("尺子自检：把启动调用注释掉，扫描器必须报 mount 没启动观察器", () => {
  const broken = SAMPLE_SRC.replace(
    /^([ \t]*)this\._observeCardList\(\);[ \t]*$/m,
    "$1// this._observeCardList();",
  );
  assert.notStrictEqual(broken, SAMPLE_SRC, "注释掉调用的补丁没生效，用例没意义");
  const problems = scanFirstPaint(broken);
  assert.ok(
    problems.some((p) => /mount\(\) 没有启动/.test(p)),
    "调用被注释掉后扫描器没报 mount 未启动，断言恒真：" + JSON.stringify(problems),
  );
});

test("尺子自检：必须抽得到 _observeCardList", () => {
  const src = readFilterBar();
  assert.ok(extractFunction(src, "_observeCardList() {"), "抽不到 _observeCardList");
  assert.ok(extractFunction(src, "mount(parentEl, context) {"), "抽不到 mount()");
  assert.ok(extractFunction(src, "_bindCardEnhancements() {"), "抽不到 _bindCardEnhancements()");
});

/* ---------- 缺陷本体 ---------- */

test("D1：必须盯住列表容器，新卡片才有入口", () => {
  const problems = scanFirstPaint(readFilterBar());
  assert.deepStrictEqual(problems, [], problems.join("\n"));
});

test("D2：入口按钮本身不能消失", () => {
  const src = readFilterBar();
  assert.ok(/查看解析/.test(src), "「查看解析」按钮没了");
  assert.ok(/eagle-sol-content/.test(src), "解析框没了");
});
