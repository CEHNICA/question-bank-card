/* 第十九轮守护测试：经典题库页的卡片墙不能每加一页就全量重画一遍。
 *
 * 实测（Playwright，1280px，真实题库 810 题）：
 *   第 1 页追加：API 只花 171ms，主线程却有一个 302ms 的长任务
 *   第 8 页追加：API 206ms，长任务涨到 892ms —— 线性增长
 * 原因在 render()：每次都先 ui.list.replaceChildren() 把整面卡片墙从 DOM 上摘下来，
 * 再把（复用的）节点一张张插回去。卡片越多，这趟拆装越贵，用户每滚一屏就卡一下。
 *
 * 修法：render() 里比对新顺序节点数组与当前 DOM children ——
 *   当前 children 是新数组的严格前缀 → 只把尾部新增的 append 进去，已有卡片原地不动；
 *   否则（顺序变了、卡片被删了、筛选变了）仍旧走 replaceChildren 全量替换。
 * 兜底必须留着，不然筛选后旧卡片会留在页面上。
 *
 * 尺子自带自检：把修复抠掉，扫描器必须报出问题，否则断言恒真。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = __dirname;
// 看门狗/回归时可用 QB_LIB_JS 指向临时副本（喂旧版本自证看门狗）。
const LIB_JS = process.env.QB_LIB_JS || path.join(FRONTEND, "library.js");

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

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^[ \t]*\/\/.*$/gm, " ");
}

// 扫描 render() 函数体，返回问题清单
function scanRender(body) {
  const problems = [];
  if (!body) return ["抽不出 render()"];
  const code = stripComments(body);

  // 1) 旧的写法：先无条件把整面墙清空（无参 replaceChildren）
  if (/ui\.list\.replaceChildren\(\s*\)/.test(code)) {
    problems.push("无条件清空整面卡片墙（ui.list.replaceChildren() 无参）");
  }
  // 2) 必须拿当前 DOM 的 children 做前缀比对，否则没法判断「只多了尾部」
  if (!/ui\.list\.children/.test(code)) {
    problems.push("没有拿当前 children 做增量比对");
  }
  // 3) 必须有只追加尾部新节点的分支
  if (!/ui\.list\.append\(/.test(code)) {
    problems.push("没有只追加尾部新节点的增量分支");
  }
  // 4) 全量替换的兜底必须留着（筛选变化、卡片删除时用）
  if (!/ui\.list\.replaceChildren\(/.test(code)) {
    problems.push("缺少全量替换兜底");
  }
  // 注：「旧节点是否还挂在列表里」这条由 D2 单独查，不在这里重复 ——
  // 同一件事在两处断言里查，退回时会一次红两条，看着像重叠。
  return problems;
}

/* ---------- 尺子自检用的合成源码 ---------- */

// 旧写法：先清空，再全部插回去
const SAMPLE_OLD = `
function render() {
  ui.list.replaceChildren();
  const fragment = document.createDocumentFragment();
  shown.forEach((item) => {
    const kept = previous.get(item.id);
    const node = kept && kept.signature === signature ? kept.node : card(item);
    fragment.append(node);
  });
  ui.list.append(fragment);
}
`;

// 新写法：前缀相同就只追加尾部
const SAMPLE_NEW = `
function render() {
  const previous = state.cards || new Map();
  const nodes = [];
  shown.forEach((item) => {
    const kept = previous.get(item.id);
    const node = kept && kept.signature === signature && kept.node.parentNode === ui.list
      ? kept.node : card(item);
    nodes.push(node);
  });
  const current = Array.from(ui.list.children);
  const canAppend = current.length <= nodes.length && current.every((child, i) => child === nodes[i]);
  if (canAppend) {
    for (let i = current.length; i < nodes.length; i++) ui.list.append(nodes[i]);
  } else {
    ui.list.replaceChildren(...nodes);
  }
}
`;

/* ---------- 测试 ---------- */

const libJs = fs.readFileSync(LIB_JS, "utf8");

test("尺子自检：旧写法必须被报出来，新写法必须干净", () => {
  const oldProblems = scanRender(SAMPLE_OLD);
  assert.ok(oldProblems.length >= 1, "旧写法（先清空再全量插回）必须被报出问题");
  assert.ok(
    oldProblems.some((p) => p.includes("清空")),
    "必须精确指出「无条件清空整面卡片墙」"
  );
  assert.deepStrictEqual(scanRender(SAMPLE_NEW), [], "新写法不该报问题");
});

test("D1 render() 不再无条件清空整面卡片墙", () => {
  const body = extractFunction(libJs, "function render() {");
  assert.ok(body, "抽不出 render()");
  const problems = scanRender(body);
  assert.deepStrictEqual(problems, [], "render() 还有这些问题：" + problems.join("、"));
});

test("D2 render() 复用的旧卡片节点必须校验还在列表里", () => {
  const body = extractFunction(libJs, "function render() {");
  assert.ok(body, "抽不出 render()");
  const code = stripComments(body);
  assert.ok(
    /isConnected/.test(code) || /parentNode\s*===\s*ui\.list/.test(code),
    "复用节点前必须确认它还挂在列表里，否则会凭空少卡片"
  );
});

test("D3 追加加载仍走 state.items 的 concat（不能改成替换）", () => {
  assert.ok(
    /state\.items\s*=\s*append\s*\?\s*state\.items\.concat\(body\.items\)\s*:\s*body\.items/.test(
      stripComments(libJs)
    ),
    "追加加载必须保留原有条目再往后接"
  );
});

test("D4 卡片内容变了要能重建（复用逻辑不能写死）", () => {
  const body = extractFunction(libJs, "function render() {");
  assert.ok(body, "抽不出 render()");
  assert.ok(
    /kept\.signature\s*===\s*signature/.test(stripComments(body)),
    "必须靠 signature 判断旧卡片能不能复用"
  );
});
