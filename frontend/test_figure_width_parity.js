// 第三十一轮：预览里的图有多宽，导出的 Word 里就得有多宽。
//
// 这轮体检在组卷预览 ↔ 导出 Word 的链路上实测出一条真缺陷：
//   选项里的图在屏幕上会被选项栏宽收口（CSS 的 max-width: 100%，栏里放不下就缩到栏宽）。
//   实测「不是柱体的是」那道题：选项 C 的图自然 205 像素宽（150dpi = 34.73 毫米），
//   预览里被缩到 116.72 像素（= 30.88 毫米），Word 里却照自然尺寸排成 34.73 毫米 ——
//   差 3.85 毫米。老师核对的是屏幕，拿到手的是文件。
//
// 根因：预览的 exam-layout.js 只按正文宽（A4 的 178 毫米）算图宽，不知道选项栏
// 有多窄；CSS 事后把图夹窄了，Word 那边没有这一步。
//
// 修法是「预览即真相」：预览把每张图在纸面上的实际宽度（毫米）量出来，随导出
// 请求交给后端，Word 照它排；没有实测值时（老前端、PDF 路径）仍按自然尺寸排 ——
// 那条路本来就跟预览一致，不能被这条修复带歪。
//
// 守护测试用 vm 沙箱真跑 measuredFigureWidths；实测那部分由 _audit31.py 负责。
// 用 QB_LIB_JS / QB_EXPORT_JS / QB_EXPORT_PY / QB_DRAFTS_PY 喂旧版本可以自证看门狗有效。

const path = require("node:path");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const assert = require("node:assert/strict");

const ROOT = path.resolve(__dirname, "..");
function read(envName, rel) {
  return fs.readFileSync(process.env[envName] || path.join(ROOT, rel), "utf8");
}
const libJs = read("QB_LIB_JS", "frontend/library.js");
const exportJs = read("QB_EXPORT_JS", "frontend/exam-export.js");
const exportPy = read("QB_EXPORT_PY", "backend/core/library_export.py");
const draftsPy = read("QB_DRAFTS_PY", "backend/core/library_drafts.py");

// 找出 needle 在源码里出现处的所有片段。
// 不能只取「第一次出现」：导出模块里同一份键可能出现在兜底赋值处
// （第三十八轮加的 fillMeasured 就排在过滤代码前面），
// 只看第一处会把真正的过滤代码判成「没有」。
function blocksAround(source, needle, window) {
  const blocks = [];
  let at = source.indexOf(needle);
  while (at >= 0) {
    blocks.push(source.slice(at, at + window));
    at = source.indexOf(needle, at + 1);
  }
  return blocks;
}

// 从源码里抠出 measuredFigureWidths 的函数体（配对花括号）。
function extractFunction(src, name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) return null;
  const open = src.indexOf("{", start);
  if (open < 0) return null;
  let depth = 0;
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === "{") depth += 1;
    else if (src[i] === "}") {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return null;
}

// 预览页带 transform: scale(factor)，屏幕上的矩形宽度 = 纸面宽度 × factor。
// 假 DOM 得照着缩放，否则量出来的数天然就是纸面宽度，这一条就成了恒真的假指标。
let CURRENT_SCALE = 1;

function fakeImage(layoutPx) {
  return { getBoundingClientRect: () => ({ width: layoutPx * CURRENT_SCALE }) };
}

function fakeOption(letter, images) {
  return {
    querySelector: (sel) => (sel === ".qb-option-label" ? { textContent: letter + "." } : null),
    querySelectorAll: (sel) => (sel === ".qb-figure img" ? images : []),
  };
}

function fakeBlock({ id, stem = [], options = [], fragment = false }) {
  return {
    dataset: { questionId: id },
    classList: { contains: (cls) => cls === "exam-fragment" && fragment === true },
    querySelectorAll: (sel) => {
      if (sel === ".qb-figures .qb-figure img") return stem;
      if (sel === ".qb-option") return options;
      return [];
    },
  };
}

function fakePaper(blocks, scale = "1") {
  return {
    style: { getPropertyValue: (name) => (name === "--exam-scale" ? scale : "") },
    querySelectorAll: (sel) => (sel === ".print-question" ? blocks : []),
  };
}

// 在沙箱里真跑一次 measuredFigureWidths(纸面)。
function runMeasured(blocks, scale = "1") {
  const source = extractFunction(libJs, "measuredFigureWidths");
  assert.ok(source, "尺子自检：library.js 里找不到 measuredFigureWidths（改名了？这条断言要重写）");
  const context = { ui: { paper: fakePaper(blocks, scale) } };
  CURRENT_SCALE = Number(scale) || 1;
  vm.createContext(context);
  vm.runInContext(source + "\nglobalThis.__measured = measuredFigureWidths();", context);
  return context.__measured;
}

// ── 0. 尺子自检 ────────────────────────────────────────────────────
test("尺子自检：能抠出并跑起 measuredFigureWidths", () => {
  const source = extractFunction(libJs, "measuredFigureWidths");
  assert.ok(source, "尺子自检：library.js 里找不到 function measuredFigureWidths(");
  assert.match(source, /getBoundingClientRect/,
    "尺子自检：函数体里没见 getBoundingClientRect，尺子对数据来源的认知已经过时");
  // 反向自检：查找器对不存在的东西必须返回 null。
  assert.equal(extractFunction(libJs, "measuredFigureWidthsXYZ"), null,
    "尺子自检：查找器对不存在的函数也返回了东西");
});

// ── 1. 题干配图量的是渲染宽度，不是布局打算给的宽度 ────────────────
test("预览：题干配图量到的是屏幕上的实际宽度（毫米）", () => {
  const measured = runMeasured([
    fakeBlock({ id: "q1", stem: [fakeImage(672.8)] }),
  ]);
  assert.ok(measured.q1 && measured.q1.stem, "题干配图没量到：Word 就退回自然尺寸，屏幕上核对过的图不是那张图");
  assert.ok(Math.abs(measured.q1.stem[0] - 178) < 0.2,
    `满栏大图应量到 178 毫米左右，实际量到 ${measured.q1.stem[0]}`);
});

// ── 2. 选项配图按选项字母分别记 ────────────────────────────────────
test("预览：选项里的图按 A/B/C/D 各自记一条", () => {
  const measured = runMeasured([
    fakeBlock({
      id: "q1",
      options: [
        fakeOption("A", [fakeImage(131.2)]),
        fakeOption("C", [fakeImage(116.72)]),
      ],
    }),
  ]);
  assert.ok(measured.q1 && measured.q1.A && measured.q1.C, "选项配图没按字母记：Word 不知道该把哪张图收口");
  assert.ok(Math.abs(measured.q1.A[0] - 34.73) < 0.2, `A 图应量到约 34.7 毫米，实际 ${measured.q1.A[0]}`);
  assert.ok(Math.abs(measured.q1.C[0] - 30.88) < 0.2, `C 图应量到约 30.9 毫米，实际 ${measured.q1.C[0]}`);
});

// ── 3. 预览页是缩放过的，量到的宽度要除掉缩放系数 ──────────────────
test("预览：预览页缩放过时，量到的宽度要除掉缩放系数", () => {
  const blocks = [fakeBlock({ id: "q1", stem: [fakeImage(672.8)] })];
  const full = runMeasured(blocks, "1");
  const half = runMeasured(blocks, "0.5");
  assert.ok(Math.abs(full.q1.stem[0] - 178) < 0.2, "缩放系数为 1 时应量到纸面宽度");
  assert.ok(Math.abs(half.q1.stem[0] - 178) < 0.2,
    `缩放系数为 0.5 时量到的仍是 ${half.q1.stem[0]}：量到的是屏幕上缩放后的尺寸，不是纸面宽度`);
});

// ── 4. 跨页续片不算数 ────────────────────────────────────────────
test("预览：跨页续片（.exam-fragment）的图不计入", () => {
  const measured = runMeasured([
    fakeBlock({ id: "q1", stem: [fakeImage(672.8)] }),
    fakeBlock({ id: "q1", stem: [fakeImage(10)], fragment: true }),
  ]);
  assert.equal((measured.q1.stem || []).length, 1, "跨页续片把整道题的图宽改了");
  assert.ok(Math.abs(measured.q1.stem[0] - 178) < 0.2, "续片里的半张图不该覆盖整道题的图宽");
});

// ── 5. 量不到的东西不进表 ────────────────────────────────────────
test("预览：没有图的题不进表，宽度为 0 的图不进表", () => {
  const measured = runMeasured([
    fakeBlock({ id: "no-figure" }),
    fakeBlock({ id: "zero", stem: [fakeImage(0)] }),
    fakeBlock({ id: "good", stem: [fakeImage(672.8)] }),
  ]);
  assert.equal(measured["no-figure"], undefined, "没有图的题不该出现在实测表里");
  assert.equal(measured.zero, undefined, "宽度为 0 的图不该进表");
  assert.ok(measured.good, "有图的题必须量到");
});

// ── 6. 导出必须带上实测图宽 ──────────────────────────────────────
test("导出：exportPaper 必须把实测图宽交给后端", () => {
  assert.match(libJs, /measured_figure_widths\s*:\s*measuredFigureWidths\(\)/,
    "导出请求里没有 measured_figure_widths：预览量到的图宽没送出去，"
    + "Word 照自然尺寸排，选项里的图比屏幕上大了一圈");
});

// ── 7. 导出端必须按本次选题过滤 ──────────────────────────────────
test("导出：measured_figure_widths 只保留本次选中的题", () => {
  const blocks = blocksAround(exportJs, "options.measured_figure_widths", 900);
  assert.ok(blocks.length, "尺子自检：exam-export.js 里找不到 options.measured_figure_widths");
  assert.ok(blocks.some(block => /selectedIds\.has\(id\)/.test(block)),
    "measured_figure_widths 没按本次选中的题过滤：没选中的题也带着图宽过去");
  assert.ok(blocks.some(block => /Number\.isFinite\(value\)/.test(block)),
    "measured_figure_widths 没校验宽度是不是数字：脏值会一路带到后端");
});

// ── 8. 后端必须按实测宽度收口 ────────────────────────────────────
test("后端：Word 按实测宽度收口，没有实测值才按自然尺寸", () => {
  assert.match(exportPy, /measured_figure_widths/,
    "后端不认实测图宽：还是照自然尺寸排，选项里的图比屏幕上大 3.8 毫米");
  assert.match(exportPy, /min\(width,\s*measured\[index\]\)/,
    "后端没有把宽度按实测值收口");
  assert.match(exportPy, /measured=.*get\(letter\)/,
    "选项配图没有拿到自己那个位置的实测宽度");
});

// ── 9. 打印设置白名单要放行新键 ──────────────────────────────────
test("后端：打印设置白名单里有 measured_figure_widths（不然请求直接被拒）", () => {
  const at = draftsPy.indexOf("PRINT_DEFAULTS");
  assert.ok(at > 0, "尺子自检：library_drafts.py 里找不到 PRINT_DEFAULTS");
  const block = draftsPy.slice(at, at + 1800);
  assert.match(block, /"measured_figure_widths"/,
    "PRINT_DEFAULTS 里没有 measured_figure_widths：_print_options() 是白名单校验，"
    + "前端一传实测图宽就被「打印设置包含不支持的选项」拒掉");
});
