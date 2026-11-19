// 第七十一轮：预览把答题留白切成几段，Word 必须照这个分段写。
//
// 实测出来的缺陷（40 题对照卷，真 Chrome + 真 Word）：
//   预览：题 6 的留白在页底被切成 24.6 + 5.4 毫米，续页上那 5.4 毫米
//         前面还打一行「第 6 题答题区（续）」；题 13 是整块 30 毫米挪到续页。
//   Word：原来一律把 30 毫米切成 20.1 + 9.9，切点跟预览不是一回事，
//         续页上那行提示压根没有 —— 学生在续页上看到一块答题区，不知道是哪道题的。
//
// 修法：前端把预览量好的分段（哪一份在首页、哪一份在续页）送进 print_options，
// 后端照着写。
//
// 守护测试用 vm 沙箱真跑这两份测量函数（exam-export.js 与 library.js 各写一遍，
// 两条导出路径都要用），必须逐字一致。

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
// 预览的分页容器（.exam-page-body）里放的是题块；题块里的 .print-answer-space
// 是留白块，高度是布局尺寸（像素）。
// 30 毫米 = 30 * 96 / 25.4 = 113.39 像素；5.4 毫米 = 20.41 像素。
function fakeBlank(heightPx) {
  return { height: String(heightPx) };
}

function fakeBlock({ id, blanks = [], continuation = false }) {
  const dataset = { questionId: id };
  if (continuation) dataset.continuation = "1";
  return {
    dataset,
    querySelectorAll: (sel) => (sel === ".print-answer-space" ? blanks : []),
  };
}

function fakePaper(blocks) {
  return {
    style: { getPropertyValue: () => "" },
    querySelectorAll: (sel) => {
      if (sel === ".exam-page-body") return [{ children: blocks }];
      return [];
    },
  };
}

function fakeComputedStyle(node) {
  return { height: node.height };
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
  if (!context.getComputedStyle) context.getComputedStyle = fakeComputedStyle;
  vm.createContext(context);
  vm.runInContext(sources.join("\n") + "\n" + call, context);
  return context.__out;
}

function runLibrary(name, context, call) {
  const source = extractFunction(libJs, name);
  assert.ok(source, `尺子自检：library.js 里找不到 function ${name}(`);
  if (!context.getComputedStyle) context.getComputedStyle = fakeComputedStyle;
  vm.createContext(context);
  vm.runInContext(source + "\n" + call, context);
  return context.__out;
}

// ── 0. 尺子自检 ────────────────────────────────────────────────────
test("尺子自检：能抠出并跑起两处的留白分段测量函数", () => {
  assert.ok(extractFunction(exportJs, "measureAnswerSpaceSplits"), "抠不出 measureAnswerSpaceSplits");
  assert.ok(extractFunction(libJs, "measuredAnswerSpaceSplits"), "抠不出 measuredAnswerSpaceSplits");
  assert.ok(extractFunction(exportJs, "fillMeasured"), "抠不出 fillMeasured");
  assert.equal(extractFunction(exportJs, "measureAnswerSpaceSplitsXYZ"), null,
    "尺子自检：查找器对不存在的函数也返回了东西");
});

// ── 1. 量出每段多高（毫米） ──────────────────────────────────────────
test("预览：留白被切成几段、每段多高，量出来是毫米", () => {
  const paper = fakePaper([
    fakeBlock({ id: "q1", blanks: [fakeBlank(113.39)] }),
    fakeBlock({ id: "q2", blanks: [fakeBlank(113.39), fakeBlank(20.41)] }),
  ]);
  const out = runExport(["measureAnswerSpaceSplits"], { paper },
    "globalThis.__out = measureAnswerSpaceSplits(paper);");
  assert.deepEqual(plain(out.q1), [{ mm: 30.0, continuation: false }],
    "30 毫米的留白没量成 30 毫米");
  assert.deepEqual(plain(out.q2), [{ mm: 30.0, continuation: false }, { mm: 5.4, continuation: false }],
    "两段留白没量成两段");
});

// ── 2. 续页上的那段必须标出来 ────────────────────────────────────────
test("预览：续页上的那段留白必须标成续页", () => {
  const paper = fakePaper([
    fakeBlock({ id: "q1", blanks: [fakeBlank(113.39)] }),
    fakeBlock({ id: "q1", blanks: [fakeBlank(20.41)], continuation: true }),
  ]);
  const out = runExport(["measureAnswerSpaceSplits"], { paper },
    "globalThis.__out = measureAnswerSpaceSplits(paper);");
  const pieces = plain(out.q1);
  assert.equal(pieces.length, 2, "两段留白没量成两段");
  assert.equal(pieces[0].continuation, false, "首页那一份被标成续页了");
  assert.equal(pieces[1].continuation, true, "续页那一份没标出来：Word 不会打续页标题");
});

// ── 3. 量不到就不报 ────────────────────────────────────────────────
test("预览：没有留白的题不进分段计划（省得后端拿到空计划）", () => {
  const paper = fakePaper([fakeBlock({ id: "q1" })]);
  const out = runExport(["measureAnswerSpaceSplits"], { paper },
    "globalThis.__out = measureAnswerSpaceSplits(paper);");
  assert.deepEqual(plain(out), {}, "没留白的题也进了分段计划");
});

// ── 4. 同口径：两份实现必须一致 ────────────────────────────────────
test("同口径：exam-export.js 与 library.js 量出来的留白分段必须一致", () => {
  const paper = fakePaper([
    fakeBlock({ id: "q1", blanks: [fakeBlank(113.39)] }),
    fakeBlock({ id: "q2", blanks: [fakeBlank(20.41)], continuation: true }),
  ]);
  const fromExport = runExport(["measureAnswerSpaceSplits"], { paper },
    "globalThis.__out = measureAnswerSpaceSplits(paper);");
  const fromLibrary = runLibrary("measuredAnswerSpaceSplits", { ui: { paper } },
    "globalThis.__out = measuredAnswerSpaceSplits();");
  assert.deepEqual(plain(fromExport), plain(fromLibrary),
    "两份口径不一致：题库工作台和经典题库页会导出两种留白分段");
});

// ── 5. 兜底：没人给就自己量 / 给了就不改 ──────────────────────────
test("兜底：调用方没给实测分段时，导出模块自己量一份", () => {
  const paper = fakePaper([fakeBlock({ id: "q1", blanks: [fakeBlank(113.39)] })]);
  const options = { document: "questions" };
  const out = runExport(["measureOptionColumns", "measureFigureWidths", "measureTableOffsets", "measureAnswerSpaceSplits", "fillMeasured"], { options, paper },
    "globalThis.__out = fillMeasured(options, paper);");
  assert.ok(out.measured_answer_splits && out.measured_answer_splits.q1,
    "没人给实测分段时没有自己量：Word 只能自己瞎切");
});

test("兜底：调用方自己量过的分段不许被覆盖", () => {
  const paper = fakePaper([fakeBlock({ id: "q1", blanks: [fakeBlank(113.39)] })]);
  const options = { measured_answer_splits: { q1: [{ mm: 12.5, continuation: false }] } };
  const out = runExport(["measureOptionColumns", "measureFigureWidths", "measureTableOffsets", "measureAnswerSpaceSplits", "fillMeasured"], { options, paper },
    "globalThis.__out = fillMeasured(options, paper);");
  assert.deepEqual(out.measured_answer_splits, { q1: [{ mm: 12.5, continuation: false }] },
    "调用方给的实测分段被覆盖了");
});

// ── 6. 经典题库页必须把分段放进导出请求 ────────────────────────────
test("导出：经典题库页的导出请求里必须带上实测留白分段", () => {
  assert.match(libJs, /measured_answer_splits\s*:\s*measuredAnswerSpaceSplits\(\)/,
    "经典题库页的导出选项里没有实测留白分段：Word 只能自己切，切点跟预览对不上");
});

// ── 7. 导出链路必须真的裁剪掉没选的题 ──────────────────────────────
test("导出：没选中的题，实测留白分段不许送出去", () => {
  const src = extractFunction(exportJs, "download");
  assert.ok(src, "尺子自检：抠不出 download()");
  assert.match(src, /measured_answer_splits/,
    "导出链路里没有处理实测留白分段：没选中的题的分段也会送出去");
});
