"use strict";

// 第三十二轮守护：答案卷的文本，预览 / PDF / Word 三边必须逐字一致。
//
// 尺子实测（破甲\_audit32f.py）量到六处差异，其中五处的方向是「Word 迁就预览」：
//   D1 答案卷标题   预览「练习」                   Word「练习 · 答案解析」
//   D2 空答案占位   预览「（原卷未提供答案解析）」  Word「（原卷未提供答案）」
//   D3 答案标签     预览有「答案：」               Word 没有
//   D4 inline 前缀  预览无                        Word「【答案解析】」
//   D5 AI 标记      预览「（AI 参考，未核对）」     Word「（AI 参考 · 未核对）」
// 后端那五处的守护在 backend/core/test_library_export_answer_text.py。
// 这一份管两件事：
//   1) D6：选择题答案（A~E）在预览里必须是正体，不能是斜体的 KaTeX 公式。
//      Word 一直照着 exam-export.js 的老规矩走（「choice answers stay upright
//      text」），是预览漏了这一条例外。
//   2) 三处文案常量必须一模一样：预览（frontend/library.js）、PDF
//      （backend/core/library_pdf.py）、Word（backend/core/library_export.py）。
//      各写各的迟早分家，这里直接比字面量。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const Solutions = require(process.env.QB_SOLUTIONS_JS || path.join(__dirname, "library-solutions.js"));

const EMPTY_SOLUTION_TEXT = "（原卷未提供答案解析）";
const AI_REFERENCE_NOTE = "（AI 参考，未核对）";

// ── D6：选择题答案保持正体 ────────────────────────────────────────

function fakeNode(tag, cls, text) {
  return {
    tag,
    className: cls || "",
    textContent: text === undefined ? "" : String(text),
    children: [],
    dataset: {},
    append(...kids) { this.children.push(...kids); },
    replaceChildren() { this.children = []; },
  };
}

function render(value) {
  const container = {
    children: [],
    append(...kids) { this.children.push(...kids); },
    replaceChildren() { this.children = []; },
  };
  const typeset = [];
  const QB = {
    renderTypeset(el, source) { typeset.push(source); el.textContent = `«${source}»`; },
    subQuestionParts() { return []; },
  };
  Solutions.render(container, value, { node: fakeNode, QB });
  const flat = [];
  (function walk(node) {
    if (node == null) return;
    if (node.children && node.children.length) node.children.forEach(walk);
    else if (node.textContent) flat.push(node.textContent);
  })(container);
  return { text: flat.join(""), typeset };
}

for (const answer of ["A", "B", "AB", "ACDE", " A "]) {
  const result = render({ answer, analysis: "" });
  assert.deepEqual(result.typeset, [],
    `选择题答案「${answer}」不该交给公式排版：Word 那边是正体文字，预览也得是正体`);
  assert.ok(result.text.includes(`答案：${answer.trim()}`),
    `选择题答案「${answer}」应该原样显示：${result.text}`);
}

for (const answer of ["x=1", "2", "AB C", "A、", "（A）"]) {
  const result = render({ answer, analysis: "" });
  assert.ok(result.typeset.includes(answer),
    `「${answer}」不是选择题答案，应该照常走公式排版：${JSON.stringify(result.typeset)}`);
}

// 只有真正是选项字母的答案才走正体，解析里的公式不受影响。
const withAnalysis = render({ answer: "A", analysis: "因为 x=1" });
assert.deepEqual(withAnalysis.typeset, ["因为 x=1"], "解析照常排版");
assert.ok(withAnalysis.text.includes("答案：A"));

// ── 三处文案常量必须一致 ──────────────────────────────────────────

function read(relative) {
  return fs.readFileSync(path.join(__dirname, "..", relative), "utf8");
}

// 看门狗要能拿「回退到修复前」的副本喂进来，所以每一份源码都能从环境变量换。
// 没设就用仓库里的真文件。
function readOverride(relative, variable) {
  const override = process.env[variable];
  return override ? fs.readFileSync(override, "utf8") : read(relative);
}

const surfaces = {
  "frontend/library.js": readOverride("frontend/library.js", "QB_LIB_JS"),
  "frontend/library-solutions.js": readOverride("frontend/library-solutions.js", "QB_SOLUTIONS_JS_SRC"),
  "frontend/eagle/eagle-print-preview.js": readOverride("frontend/eagle/eagle-print-preview.js", "QB_EAGLE_JS"),
  "backend/core/library_pdf.py": readOverride("backend/core/library_pdf.py", "QB_PDF_PY"),
  "backend/core/library_export.py": readOverride("backend/core/library_export.py", "QB_EXPORT_PY"),
};

// 打印标记在四个地方各写一遍（预览、Eagle 预览、PDF、Word），必须逐字相同。
// library-solutions.js 不在内：那一句是调用方塞进来的空占位文案。
for (const name of ["frontend/library.js", "frontend/eagle/eagle-print-preview.js",
  "backend/core/library_pdf.py", "backend/core/library_export.py"]) {
  assert.ok(surfaces[name].includes(AI_REFERENCE_NOTE),
    `${name} 里的 AI 标记应写成「${AI_REFERENCE_NOTE}」`);
}

// 旧写法是带全角括号的「（AI 参考 · 未核对）」。界面上那些不带括号的
// 「AI 参考 · 未核对」是按钮和提示里的说法，不是打印标记，别拿它们当证据。
for (const [name, source] of Object.entries(surfaces)) {
  assert.ok(!source.includes("（AI 参考 · 未核对）"),
    `${name} 里还留着旧的打印标记「（AI 参考 · 未核对）」，跟预览对不上`);
}

assert.ok(surfaces["frontend/library.js"].includes(EMPTY_SOLUTION_TEXT),
  "预览的空答案占位应是「（原卷未提供答案解析）」");
assert.ok(surfaces["backend/core/library_pdf.py"].includes(EMPTY_SOLUTION_TEXT),
  "PDF 的空答案占位应是「（原卷未提供答案解析）」");
assert.ok(surfaces["backend/core/library_export.py"].includes(EMPTY_SOLUTION_TEXT),
  "Word 的空答案占位应是「（原卷未提供答案解析）」");

// 注释里可以随便提旧写法（要说明「以前错在哪」），所以只在真正会跑到的
// 代码行里找残留。
function code(source) {
  return source.split(/\r?\n/)
    .filter(line => !/^\s*(#|\/\/)/.test(line))
    .join("\n");
}

// Word 的后缀标题和 inline 前缀已经去掉：预览和 PDF 里都没有这两个东西。
assert.ok(!code(surfaces["frontend/library.js"]).includes("· 答案解析"),
  "预览里不该有「· 答案解析」后缀");
assert.ok(!code(surfaces["backend/core/library_pdf.py"]).includes("· 答案解析"),
  "PDF 里不该有「· 答案解析」后缀");
assert.ok(!code(surfaces["backend/core/library_export.py"]).includes("【答案解析】"),
  "Word 里不该再有「【答案解析】」前缀");
assert.ok(!code(surfaces["frontend/library.js"]).includes("【答案解析】"),
  "预览里不该有「【答案解析】」前缀");
