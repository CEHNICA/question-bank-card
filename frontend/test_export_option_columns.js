// 第二十七轮：预览看到的选项栏数，必须和导出的 Word 一致。
//
// 这轮体检在组卷预览 ↔ 导出 Word 的链路上实测出一条真缺陷：
//   「自动排版」下前端和后端各算各的选项栏数，而且两边都不提示。
//   前端 exam-layout.js 的 fitOptions() 按真实渲染宽度量出栏数写进
//   dataset.examColumns；后端 library_export.py 的 _option_columns() 按自己的
//   em 估算另算一遍（auto 时只有 4 个选项才试 4 栏，还把「公式高度 > 2.6em」
//   当宽度用）。实测 52 题里 4 题对不上：
//     第 21 题（选项含 \dfrac）：预览 4 栏 → 导出 1 栏
//     第 28 题（选项是含公式的集合表达式）：预览 2 栏 → 导出 4 栏
//     第 51 题（选项含 \dfrac/\sqrt）：预览 2 栏 → 导出 1 栏
//     第 52 题（5 个选项）：预览 5 栏 → 导出 2 栏
//   老师核对的是屏幕，拿到手的是文件 —— 这种对不上是硬伤。
//
// 修法是「预览即真相」：预览量到的每题栏数随导出请求交给后端，Word 照屏幕排；
// 没有实测值（老前端、PDF 路径）才由后端自己估算，并且必须用
// X-QB-Layout-Warning 说清楚降级过。
//
// 守护测试用 vm 沙箱真跑 measuredOptionColumns；实测那部分由 _audit27.py 负责。
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

// 从源码里抠出 measuredOptionColumns 的函数体（配对花括号）。
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

// 极简假 DOM：只提供 measuredOptionColumns 用到的那几个接口。
function fakeBlock({ id, columns, fragment }) {
  return {
    dataset: { questionId: id },
    classList: { contains: (cls) => cls === "exam-fragment" && fragment === true },
    querySelector: (sel) => (sel === ".qb-options" ? { dataset: { examColumns: columns } } : null),
  };
}

function fakePaper(blocks) {
  return { querySelectorAll: (sel) => (sel === ".print-question" ? blocks : []) };
}

// 在沙箱里真跑一次 measuredOptionColumns(纸面)。
function runMeasured(blocks) {
  const source = extractFunction(libJs, "measuredOptionColumns");
  assert.ok(source, "尺子自检：library.js 里找不到 measuredOptionColumns（改名了？这条断言要重写）");
  const context = { ui: { paper: fakePaper(blocks) } };
  vm.createContext(context);
  vm.runInContext(source + "\nglobalThis.__measured = measuredOptionColumns();", context);
  return context.__measured;
}

// ── 0. 尺子自检 ────────────────────────────────────────────────────
test("尺子自检：能抠出并跑起 measuredOptionColumns", () => {
  const source = extractFunction(libJs, "measuredOptionColumns");
  assert.ok(source, "尺子自检：library.js 里找不到 function measuredOptionColumns(");
  assert.match(source, /data-exam-columns|dataset\.examColumns/,
    "尺子自检：函数体里没见 dataset.examColumns，尺子对数据来源的认知已经过时");
  // 反向自检：查找器对不存在的东西必须返回 null。
  assert.equal(extractFunction(libJs, "measuredOptionColumnsXYZ"), null,
    "尺子自检：查找器对不存在的函数也返回了东西");
});

// ── 1. 从预览 DOM 里读出每题栏数 ──────────────────────────────────
test("预览：栏数从 .qb-options 的 data-exam-columns 取", () => {
  const measured = runMeasured([
    fakeBlock({ id: "q1", columns: "4" }),
    fakeBlock({ id: "q2", columns: "2" }),
  ]);
  assert.deepEqual({ ...measured }, { q1: 4, q2: 2 },
    "没量到预览里的栏数：导出就退回后端自己猜，屏幕上四栏、文件里竖排");
});

// ── 2. 跨页续片不算数 ────────────────────────────────────────────
test("预览：跨页续片（.exam-fragment）的栏数不计入", () => {
  const measured = runMeasured([
    fakeBlock({ id: "q1", columns: "4" }),
    fakeBlock({ id: "q1", columns: "1", fragment: true }),
  ]);
  assert.deepEqual({ ...measured }, { q1: 4 },
    "跨页续片把整道题的栏数改掉了：一道题跨页会拆成 .exam-fragment 续片，"
    + "它只带着半道题，栏数不算数");
});

// ── 3. 非法值不入表 ──────────────────────────────────────────────
test("预览：非法栏数（0、6、小数、非数字）不进表", () => {
  const measured = runMeasured([
    fakeBlock({ id: "zero", columns: "0" }),
    fakeBlock({ id: "six", columns: "6" }),
    fakeBlock({ id: "frac", columns: "2.5" }),
    fakeBlock({ id: "nan", columns: "abc" }),
    fakeBlock({ id: "empty", columns: "" }),
    fakeBlock({ id: "good", columns: "3" }),
  ]);
  assert.deepEqual({ ...measured }, { good: 3 },
    "非法栏数进了导出请求：后端要么报错，要么排出一份对不上的卷子");
});

test("预览：dataset 的值是字符串，正常数字要认出来", () => {
  // data-exam-columns 是 DOM attribute，读出来必然是字符串。
  // 这一条是尺子的反向自检：别把合法的 "3" 也当成脏值扔了。
  const measured = runMeasured([fakeBlock({ id: "good", columns: "3" })]);
  assert.deepEqual({ ...measured }, { good: 3 },
    "字符串形式的栏数被当成了脏值：dataset 读出来永远是字符串，正常题目的栏数会全丢，"
    + "导出又退回后端自己猜");
});

// ── 4. 导出必须带上实测栏数 ──────────────────────────────────────
test("导出：exportPaper 必须把实测栏数交给后端", () => {
  assert.match(libJs, /option_columns\s*:\s*measuredOptionColumns\(\)/,
    "导出请求里没有 option_columns：预览量到的栏数没送出去，后端只能自己猜，"
    + "实测 52 题里 4 题预览与导出的栏数对不上");
});

// ── 5. 导出端必须按本次选题过滤 ──────────────────────────────────
test("导出：option_columns 只保留本次选中且合法的题", () => {
  assert.match(exportJs, /options\.option_columns/,
    "导出端没有处理 option_columns：别的卷子的栏数会被带过来");
  const blocks = blocksAround(exportJs, "options.option_columns", 600);
  assert.ok(blocks.length, "尺子自检：exam-export.js 里找不到 options.option_columns");
  assert.ok(blocks.some(block => /selectedIds\.has\(id\)/.test(block)),
    "option_columns 没按本次选中的题过滤：没选中的题也带着栏数过去");
  assert.ok(blocks.some(block => /Number\.isInteger\(value\)/.test(block)),
    "option_columns 没校验栏数是不是整数：脏值会一路带到后端");
  assert.ok(blocks.some(block => /value\s*>=\s*1\s*&&\s*value\s*<=\s*5/.test(block)),
    "option_columns 没限制栏数范围（应为 1 到 5）");
});

// ── 6. 后端必须优先用实测值 ──────────────────────────────────────
test("后端：Word 优先用实测栏数，没有实测值才自己估算", () => {
  assert.match(exportPy, /options\.get\("option_columns"\)/,
    "后端 _option_columns() 不认实测栏数：还是自己算，屏幕上四栏、文件里竖排");
  const at = exportPy.indexOf('options.get("option_columns")');
  assert.ok(at > 0, "尺子自检：library_export.py 里找不到 options.get(\"option_columns\")");
  const block = exportPy.slice(at, at + 400);
  assert.match(block, /1\s*<=\s*measured\s*<=\s*len\(letters\)/,
    "后端没有把实测栏数限制在本题实际选项数之内");
});

test("后端：实测栏数存在时不再重复发降级警告", () => {
  const at = exportPy.indexOf('options.get("option_columns")', exportPy.indexOf("def export("));
  assert.ok(at > 0, "尺子自检：export() 里找不到 options.get(\"option_columns\")");
  const block = exportPy.slice(at, at + 400);
  assert.match(block, /type\(measured\)\s*is\s*not\s*int/,
    "后端没有区分「有没有实测值」：有实测值还照旧发降级警告，"
    + "老师在已经看到屏幕排版的情况下又被告知排版被改过");
});

// ── 7. 打印设置白名单要放行新键 ──────────────────────────────────
test("后端：打印设置白名单里有 option_columns（不然请求直接被拒）", () => {
  const at = draftsPy.indexOf("PRINT_DEFAULTS");
  assert.ok(at > 0, "尺子自检：library_drafts.py 里找不到 PRINT_DEFAULTS");
  const block = draftsPy.slice(at, at + 1600);
  assert.match(block, /"option_columns"/,
    "PRINT_DEFAULTS 里没有 option_columns：_print_options() 是白名单校验，"
    + "前端一传实测栏数就被「打印设置包含不支持的选项」拒掉");
});
