// 第五十一轮：预览里那张表的横向位置，必须原样交给 Word。
//
// 这轮体检在「预览 ↔ 导出」链路上实测出一条真缺陷：
//   预览里表格的位置不是定值 —— 题干里的表在「题号右边那一列」里居中，
//   而那一列有多宽取决于题干正文有多长（正文比表窄 → 表贴左；
//   正文比表宽 → 表在正文里居中）。答案块里的表则恒在答案正文列里居中。
//   真 Chrome 实测（12pt，178 毫米版心）：
//     题干表离版心左缘 17.59 像素 = 4.66 毫米
//     答案表离版心左缘 301.69 像素 = 79.80 毫米
//   Word 原来无条件 jc=center（整栏居中）：题干表落在离版心左缘 56.9 毫米处，
//   答案表差 5.1 毫米 —— 屏幕上贴着题号右边，导出来飘到版心正中。
//
// 修法：前端量好每张表离版心左缘多远（毫米），送进 print_options，
// 后端照这个位置写 w:tblInd。
//
// 守护测试用 vm 沙箱真跑这两份测量函数；用 QB_LIB_JS / QB_EXPORT_JS
// 喂旧版本可以自证看门狗有效。

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

// 从源码里抠出一个函数的完整源码（配对花括号）。
function extractFunction(src, name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) return null;
  let index = src.indexOf("(", start);
  let parens = 0;
  while (index < src.length) {
    const ch = src[index];
    if (ch === "(") parens += 1;
    else if (ch === ")") parens -= 1;
    index += 1;
    if (parens === 0) break;
  }
  if (parens !== 0) return null;
  const open = src.indexOf("{", index);
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

// ── 假 DOM ──────────────────────────────────────────────────────────
// 正文栏（.exam-page-body / .print-flow）是 178 毫米宽的一条。
// 假 DOM 里用「版心宽 = 178 毫米 = 672.756 像素」来构造，
// 再让每张表给出自己离栏左缘多少像素，量出来的就是毫米。
const COLUMN_PX = 178 * 96 / 25.4;

function fakeTable(leftPx) {
  return {
    getBoundingClientRect: () => ({ left: leftPx, width: 100 }),
    closest: (sel) => (sel === ".exam-page-body, .print-flow" ? { getBoundingClientRect: () => ({ left: 0, width: COLUMN_PX }) } : null),
  };
}

function fakeStemBlock({ id, tables = [], fragment = false }) {
  return {
    dataset: { questionId: id },
    classList: { contains: (cls) => cls === "exam-fragment" && fragment === true },
    // fillMeasured 里还有 measureOptionColumns 一道，它查 .qb-options 的 examColumns。
    querySelector: (sel) => (sel === ".qb-options" ? { dataset: { examColumns: "1" } } : null),
    querySelectorAll: (sel) => (sel === ".qb-stem .qb-table" ? tables : []),
  };
}

function fakeAnswerRow({ id, answer = [], analysis = [] }) {
  return {
    dataset: { questionId: id },
    querySelectorAll: (sel) => {
      if (sel === ".solution-result .qb-table") return answer;
      if (sel === ".qb-analysis .qb-table") return analysis;
      return [];
    },
  };
}

function fakePaper(blocks = [], rows = []) {
  return {
    style: { getPropertyValue: (name) => (name === "--exam-scale" ? "1" : "") },
    querySelectorAll: (sel) => {
      if (sel === ".print-question") return blocks;
      if (sel === ".print-answer-row") return rows;
      return [];
    },
  };
}

function plain(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function runExport(names, context, call) {
  const sources = names.map((name) => {
    const source = extractFunction(exportJs, name);
    assert.ok(source, `尺子自检：exam-export.js 里找不到 function ${name}(`);
    return source;
  });
  vm.createContext(context);
  vm.runInContext(sources.join("\n") + "\n" + call, context);
  return context.__out;
}

function runLibrary(name, context, call) {
  const source = extractFunction(libJs, name);
  assert.ok(source, `尺子自检：library.js 里找不到 function ${name}(`);
  vm.createContext(context);
  vm.runInContext(source + "\n" + call, context);
  return context.__out;
}

// ── 0. 尺子自检 ────────────────────────────────────────────────────
test("尺子自检：能抠出并跑起两处的表格实测函数", () => {
  assert.ok(extractFunction(exportJs, "measureTableOffsets"), "抠不出 measureTableOffsets");
  assert.ok(extractFunction(libJs, "measuredTableOffsets"), "抠不出 measuredTableOffsets");
  assert.ok(extractFunction(exportJs, "fillMeasured"), "抠不出 fillMeasured");
  assert.equal(extractFunction(exportJs, "measureTableOffsetsXYZ"), null,
    "尺子自检：查找器对不存在的函数也返回了东西");
});

// ── 1. 题干表与答案表分槽量 ────────────────────────────────────────
test("预览：题干表与答案表分别量到自己的槽里", () => {
  // 4.66 毫米 = 17.59 像素；79.80 毫米 = 301.69 像素（都是真 Chrome 实测值）。
  const paper = fakePaper(
    [fakeStemBlock({ id: "q1", tables: [fakeTable(17.59)] })],
    [fakeAnswerRow({ id: "q1", answer: [fakeTable(301.69)] })],
  );
  const out = runExport(["measureTableOffsets"], { paper },
    "globalThis.__out = measureTableOffsets(paper);");
  assert.ok(Math.abs(out.q1.stem[0] - 4.66) < 0.02,
    `题干表应量到约 4.66 毫米，实际 ${out.q1.stem[0]}`);
  assert.ok(Math.abs(out.q1.answer[0] - 79.8) < 0.02,
    `答案表应量到约 79.80 毫米，实际 ${out.q1.answer[0]}`);
});

test("预览：解析里的表量到解析槽", () => {
  const paper = fakePaper([], [fakeAnswerRow({ id: "q1", analysis: [fakeTable(301.69)] })]);
  const out = runExport(["measureTableOffsets"], { paper },
    "globalThis.__out = measureTableOffsets(paper);");
  assert.ok(out.q1.analysis, "解析里的表没量到");
  assert.ok(Math.abs(out.q1.analysis[0] - 79.8) < 0.02);
});

// ── 2. 同一道题的多张表按顺序排 ────────────────────────────────────
test("预览：同一道题的多张表按出现顺序排", () => {
  const paper = fakePaper([
    fakeStemBlock({ id: "q1", tables: [fakeTable(17.59), fakeTable(50.0)] }),
  ]);
  const out = runExport(["measureTableOffsets"], { paper },
    "globalThis.__out = measureTableOffsets(paper);");
  assert.equal(out.q1.stem.length, 2);
  assert.ok(out.q1.stem[0] < out.q1.stem[1], "两张表的位置顺序反了");
});

// ── 3. 跨页续片不算数 ──────────────────────────────────────────────
test("预览：跨页续片（.exam-fragment）的表不计入", () => {
  const paper = fakePaper([
    fakeStemBlock({ id: "q1", tables: [fakeTable(17.59)] }),
    fakeStemBlock({ id: "q1", tables: [fakeTable(120.0)], fragment: true }),
  ]);
  const out = runExport(["measureTableOffsets"], { paper },
    "globalThis.__out = measureTableOffsets(paper);");
  assert.equal(out.q1.stem.length, 1, "续片把整道题的位置改了");
  assert.ok(Math.abs(out.q1.stem[0] - 4.66) < 0.02);
});

// ── 4. 量不到就不报 ────────────────────────────────────────────────
test("预览：没有栏可量时不报位置（不许瞎填）", () => {
  const paper = fakePaper([fakeStemBlock({ id: "q1", tables: [] })]);
  const out = runExport(["measureTableOffsets"], { paper },
    "globalThis.__out = measureTableOffsets(paper);");
  assert.equal(out.q1, undefined, "没有表却报了个位置");
});

// ── 5. 同口径：两份实现必须一致 ────────────────────────────────────
test("同口径：exam-export.js 与 library.js 量出来的表格位置必须一致", () => {
  const paper = fakePaper(
    [fakeStemBlock({ id: "q1", tables: [fakeTable(17.59)] })],
    [fakeAnswerRow({ id: "q1", answer: [fakeTable(301.69)], analysis: [fakeTable(200.0)] })],
  );
  const fromExport = runExport(["measureTableOffsets"], { paper },
    "globalThis.__out = measureTableOffsets(paper);");
  const fromLibrary = runLibrary("measuredTableOffsets", { ui: { paper } },
    "globalThis.__out = measuredTableOffsets();");
  assert.deepEqual(plain(fromExport), plain(fromLibrary),
    "两份口径不一致：题库工作台和经典题库页会导出两种位置");
});

// ── 6. 兜底：没人给就自己量 / 给了就不改 ──────────────────────────
test("兜底：调用方没给实测位置时，导出模块自己量一份", () => {
  const paper = fakePaper([fakeStemBlock({ id: "q1", tables: [fakeTable(17.59)] })]);
  const options = { document: "questions" };
  const out = runExport(["measureOptionColumns", "measureFigureWidths", "measureTableOffsets", "fillMeasured"],
    { options, paper },
    "globalThis.__out = fillMeasured(options, paper);");
  assert.ok(out.measured_table_offsets && out.measured_table_offsets.q1,
    "没人给实测位置时没有自己量：Word 只能整栏居中");
});

test("兜底：调用方自己量过的位置不许被覆盖", () => {
  const paper = fakePaper([fakeStemBlock({ id: "q1", tables: [fakeTable(17.59)] })]);
  const options = { measured_table_offsets: { q1: { stem: [12.5] } } };
  const out = runExport(["measureOptionColumns", "measureFigureWidths", "measureTableOffsets", "fillMeasured"],
    { options, paper },
    "globalThis.__out = fillMeasured(options, paper);");
  assert.deepEqual(out.measured_table_offsets, { q1: { stem: [12.5] } },
    "调用方给的实测位置被覆盖了");
});

// ── 7. 经典题库页必须把实测位置放进导出请求 ────────────────────────
test("导出：经典题库页的导出请求里必须带上实测表格位置", () => {
  assert.match(libJs, /measured_table_offsets\s*:\s*measuredTableOffsets\(\)/,
    "经典题库页的导出选项里没有实测表格位置：Word 只能整栏居中");
});

// ── 8. 导出链路必须真的裁剪掉没选的题 ────────────────────────────
test("导出：没选中的题，实测位置不许送出去", () => {
  const src = extractFunction(exportJs, "download");
  assert.ok(src, "尺子自检：抠不出 download()");
  assert.match(src, /measured_table_offsets/,
    "导出链路里没有处理实测表格位置：没选中的题的位置也会送出去");
});
