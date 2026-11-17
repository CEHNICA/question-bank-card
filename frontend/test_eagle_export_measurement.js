// 第三十八轮：题库工作台（Eagle）导出的 Word，必须和它的 A4 预览一模一样。
//
// 这轮体检在「预览 ↔ 导出」链路上实测出一条真缺陷：
//   题库工作台的 A4 预览跑在 iframe 里，父页面从来没量过「每道题排成几栏」
//   和「每张图在纸面上多宽」，于是导出的 Word 只能让后端自己估。
//   实测同一批 9 道题：
//     · 栏数：预览 [4,4,4,4,4,2,2,1,1]，导出的 Word 是 [4,2,4,4,4,4,2,1,1]
//       —— 两道题对不上（屏幕上两栏，文件里四栏）。
//     · 图宽：预览 30.88 毫米的图，Word 里排成 55.71 毫米，大了八成。
//   经典题库页那条路没这个问题：它自己量好了再交给后端。
//
// 修法：调用方没给实测值时，导出模块自己从眼前这份预览里量一份
// （exam-export.js 的 fillMeasured / previewPaper）。
//
// 守护测试用 vm 沙箱真跑这几份函数；实测那部分由 _audit38.py 负责。
// 用 QB_LIB_JS / QB_EXPORT_JS 喂旧版本可以自证看门狗有效。

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
  // 先跳过参数列表 —— 参数里可能有 { } 解构（如 download(items, { title, ... })），
  // 直接找第一个 { 会把解构的括号当成函数体，抠出来的就只有一行签名。
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
// 预览页带 transform: scale(factor)，屏幕上的矩形宽度 = 纸面宽度 × factor。
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

function fakeBlock({ id, stem = [], options = [], columns = 0, fragment = false }) {
  const list = { dataset: { examColumns: String(columns) } };
  return {
    dataset: { questionId: id },
    classList: { contains: (cls) => cls === "exam-fragment" && fragment === true },
    querySelector: (sel) => (sel === ".qb-options" ? list : null),
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

// vm 沙箱里的对象带着另一个 realm 的 Object.prototype，
// deepStrictEqual 会因为原型不同判「不相等」——值一样也算失败。
// 所以比较之前统一转成当前 realm 的普通对象。
function plain(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

// 在沙箱里真跑 exam-export.js 里的一个函数。
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

// 在沙箱里真跑 library.js 里的一个函数（经典题库页那份口径）。
function runLibrary(name, context, call) {
  const source = extractFunction(libJs, name);
  assert.ok(source, `尺子自检：library.js 里找不到 function ${name}(`);
  vm.createContext(context);
  vm.runInContext(source + "\n" + call, context);
  return context.__out;
}

// ── 0. 尺子自检 ────────────────────────────────────────────────────
test("尺子自检：能抠出并跑起 exam-export.js 的实测函数", () => {
  assert.ok(extractFunction(exportJs, "measureOptionColumns"), "抠不出 measureOptionColumns");
  assert.ok(extractFunction(exportJs, "measureFigureWidths"), "抠不出 measureFigureWidths");
  assert.ok(extractFunction(exportJs, "fillMeasured"), "抠不出 fillMeasured");
  assert.ok(extractFunction(exportJs, "previewPaper"), "抠不出 previewPaper");
  // 反向自检：查找器对不存在的东西必须返回 null。
  assert.equal(extractFunction(exportJs, "measureOptionColumnsXYZ"), null,
    "尺子自检：查找器对不存在的函数也返回了东西");
});

test("尺子自检：导出模块必须把实测函数挂在返回值上（否则调用方够不着）", () => {
  assert.match(exportJs, /measureOptionColumns[^\n]*measureFigureWidths/,
    "exam-export.js 的返回值里没有实测函数：兜底量出来的值送不出去");
});

// ── 1. 栏数：照预览写在选项表上的那个数 ────────────────────────────
test("预览：栏数照 exam-layout 量在选项表上的 examColumns 取", () => {
  const blocks = [
    fakeBlock({ id: "q1", columns: 4 }),
    fakeBlock({ id: "q2", columns: 2 }),
    fakeBlock({ id: "q3", columns: 1 }),
  ];
  const out = runExport(["measureOptionColumns"], { paper: fakePaper(blocks) },
    "globalThis.__out = measureOptionColumns(paper);");
  assert.deepEqual(plain(out), { q1: 4, q2: 2, q3: 1 });
});

// ── 2. 跨页续片不算数 ──────────────────────────────────────────────
test("预览：跨页续片（.exam-fragment）的栏数不计入", () => {
  const blocks = [
    fakeBlock({ id: "q1", columns: 4 }),
    fakeBlock({ id: "q1", columns: 1, fragment: true }),
  ];
  const out = runExport(["measureOptionColumns"], { paper: fakePaper(blocks) },
    "globalThis.__out = measureOptionColumns(paper);");
  assert.deepEqual(plain(out), { q1: 4 }, "续片把整道题的栏数改了");
});

// ── 3. 不合法的栏数不进表 ──────────────────────────────────────────
test("预览：越界或非整数的栏数不进表", () => {
  const blocks = [
    fakeBlock({ id: "bad", columns: 0 }),
    fakeBlock({ id: "big", columns: 9 }),
    fakeBlock({ id: "none", columns: 0 }),
    fakeBlock({ id: "good", columns: 3 }),
  ];
  const out = runExport(["measureOptionColumns"], { paper: fakePaper(blocks) },
    "globalThis.__out = measureOptionColumns(paper);");
  assert.equal(out.bad, undefined, "0 栏不是合法值");
  assert.equal(out.big, undefined, "9 栏越界（最多 5 栏）");
  assert.equal(out.good, 3);
});

// ── 4. 图宽：除掉缩放系数，按位置分槽 ──────────────────────────────
test("预览：图宽除掉缩放系数，选项里的图按字母分槽", () => {
  const blocks = [
    fakeBlock({
      id: "q1",
      stem: [fakeImage(672.8)],
      options: [fakeOption("A", [fakeImage(131.2)]), fakeOption("C", [fakeImage(116.72)])],
    }),
  ];
  CURRENT_SCALE = 1;
  const out = runExport(["measureFigureWidths"], { paper: fakePaper(blocks, "1") },
    "globalThis.__out = measureFigureWidths(paper);");
  assert.ok(Math.abs(out.q1.stem[0] - 178) < 0.2, `题干图应量到约 178 毫米，实际 ${out.q1.stem[0]}`);
  assert.ok(Math.abs(out.q1.A[0] - 34.73) < 0.2, `A 图应量到约 34.7 毫米，实际 ${out.q1.A[0]}`);
  assert.ok(Math.abs(out.q1.C[0] - 30.88) < 0.2, `C 图应量到约 30.9 毫米，实际 ${out.q1.C[0]}`);
});

test("预览：预览页缩放过时，量到的宽度要除掉缩放系数", () => {
  const blocks = [fakeBlock({ id: "q1", stem: [fakeImage(672.8)] })];
  CURRENT_SCALE = 0.5;
  const out = runExport(["measureFigureWidths"], { paper: fakePaper(blocks, "0.5") },
    "globalThis.__out = measureFigureWidths(paper);");
  assert.ok(Math.abs(out.q1.stem[0] - 178) < 0.2,
    `缩放系数 0.5 时量到的仍是 ${out.q1.stem[0]}：量到的是屏幕上缩放后的尺寸`);
});

// ── 5. 同口径：和经典题库页那份必须一模一样 ────────────────────────
// 两份实现各写一遍是隐患：改了一份忘了另一份，两边就各量各的。
// 这条测试把两份放在同一个假 DOM 上跑，逐字对比结果。
test("同口径：exam-export.js 的实测与 library.js 的实测结果必须一致", () => {
  const blocks = [
    fakeBlock({ id: "q1", stem: [fakeImage(672.8)], columns: 4,
      options: [fakeOption("A", [fakeImage(131.2)]), fakeOption("B", [fakeImage(0)])] }),
    fakeBlock({ id: "q2", columns: 2, fragment: false }),
    fakeBlock({ id: "q2", columns: 1, fragment: true }),
    fakeBlock({ id: "q3", columns: 1 }),
  ];
  CURRENT_SCALE = 1;
  const paper = fakePaper(blocks, "1");
  const fromExport = runExport(["measureOptionColumns", "measureFigureWidths"], { paper },
    "globalThis.__out = {c: measureOptionColumns(paper), w: measureFigureWidths(paper)};");
  const fromLibrary = runLibrary("measuredOptionColumns", { ui: { paper } },
    "globalThis.__out = measuredOptionColumns();");
  const widthsFromLibrary = runLibrary("measuredFigureWidths", { ui: { paper } },
    "globalThis.__out = measuredFigureWidths();");
  assert.deepEqual(plain(fromExport.c), plain(fromLibrary), "两份栏数口径不一致：题库工作台和经典题库页会排成不一样");
  assert.deepEqual(plain(fromExport.w), plain(widthsFromLibrary), "两份图宽口径不一致：同一份预览会导出两种尺寸");
});

// ── 6. 兜底：没人给就自己量 ────────────────────────────────────────
test("兜底：调用方没给实测值时，导出模块自己量一份", () => {
  const blocks = [fakeBlock({ id: "q1", stem: [fakeImage(672.8)], columns: 4 })];
  CURRENT_SCALE = 1;
  const options = { document: "questions" };
  const out = runExport(["measureOptionColumns", "measureFigureWidths", "measureTableOffsets", "fillMeasured"],
    { options, paper: fakePaper(blocks, "1") },
    "globalThis.__out = fillMeasured(options, paper);");
  assert.deepEqual(plain(out.option_columns), { q1: 4 }, "没人给实测栏数时没有自己量：Word 只能自己猜");
  assert.ok(out.measured_figure_widths.q1, "没人给实测图宽时没有自己量：选项里的图会比屏幕上大一圈");
});

// ── 7. 兜底：调用方给了就一个字都不改 ──────────────────────────────
test("兜底：调用方自己量过的实测值不许被覆盖", () => {
  const blocks = [fakeBlock({ id: "q1", stem: [fakeImage(672.8)], columns: 4 })];
  CURRENT_SCALE = 1;
  const options = { option_columns: { q1: 2 }, measured_figure_widths: { q1: { stem: [12.5] } } };
  const out = runExport(["measureOptionColumns", "measureFigureWidths", "measureTableOffsets", "fillMeasured"],
    { options, paper: fakePaper(blocks, "1") },
    "globalThis.__out = fillMeasured(options, paper);");
  assert.deepEqual(out.option_columns, { q1: 2 }, "调用方给的实测栏数被覆盖了");
  assert.deepEqual(out.measured_figure_widths, { q1: { stem: [12.5] } }, "调用方给的实测图宽被覆盖了");
});

// ── 8. 没有预览时不动手 ────────────────────────────────────────────
test("兜底：眼前没有排好版的预览时，什么都不该往请求里塞", () => {
  const options = { document: "questions" };
  const out = runExport(["measureOptionColumns", "measureFigureWidths", "measureTableOffsets", "fillMeasured"],
    { options, paper: null },
    "globalThis.__out = fillMeasured(options, null);");
  assert.equal(out.option_columns, undefined, "没有预览却塞了栏数：后端会拿到一份来路不明的值");
  assert.equal(out.measured_figure_widths, undefined, "没有预览却塞了图宽");
  assert.equal(out.measured_table_offsets, undefined, "没有预览却塞了表格位置");
});

// ── 9. 找预览：同源 iframe 里那张纸 ────────────────────────────────
test("找预览：能在同源 iframe 里找到排好版的纸", () => {
  const paper = { querySelector: () => ({}) };
  const doc = { querySelector: (sel) => (sel === ".print-paper[data-page-count]" ? paper : null) };
  const context = {
    root: {
      document: {
        querySelector: () => null,
        querySelectorAll: () => [{ contentDocument: doc }],
      },
    },
  };
  const out = runExport(["previewPaper"], context, "globalThis.__out = previewPaper();");
  assert.equal(out, paper, "同源 iframe 里的预览没找到：题库工作台的预览就在 iframe 里");
});

test("找预览：哪儿都没有预览时返回 null，不许抛", () => {
  const context = {
    root: { document: { querySelector: () => null, querySelectorAll: () => [] } },
  };
  const out = runExport(["previewPaper"], context, "globalThis.__out = previewPaper();");
  assert.equal(out, null);
});

test("找预览：跨源 iframe 不许把整条链路带崩", () => {
  const context = {
    root: {
      document: {
        querySelector: () => null,
        querySelectorAll: () => [{
          get contentDocument() { throw new Error("cross origin"); },
        }],
      },
    },
  };
  const out = runExport(["previewPaper"], context, "globalThis.__out = previewPaper();");
  assert.equal(out, null);
});

test("找预览：document 上没有 querySelector 时不许抛", () => {
  // 无头导出环境里 document 只是个残缺的对象。找不到预览只是量不到实测值，
  // 不能把整条导出带崩。
  const context = {
    root: { document: { body: {}, createElement: () => ({}) } },
  };
  const out = runExport(["previewPaper"], context, "globalThis.__out = previewPaper();");
  assert.equal(out, null, "残缺的 document 让预览查找抛了：整个导出都废了");
});

// ── 10. 导出链路必须真的调它 ───────────────────────────────────────
test("导出：download() 必须把实测值填进请求", () => {
  // 只看整个文件会误判：函数定义那一行也叫 fillMeasured(options, paper)。
  // 必须抠出 download() 的函数体，确认调用点真在里面。
  const downloadSrc = extractFunction(exportJs, "download");
  assert.ok(downloadSrc, "尺子自检：抠不出 download()");
  assert.match(downloadSrc, /fillMeasured\(\s*options\s*,\s*paper\s*\)/,
    "download() 没有调用 fillMeasured：没人给实测值时不会自己量，Word 只能自己猜");
  assert.match(downloadSrc, /previewPaper\(\)/,
    "download() 没有找预览：题库工作台的预览在 iframe 里，不找就量不到");
});
