"use strict";
// 题库页助手工作台的字符级 Diff 差异高亮测试
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const js = fs.readFileSync(require.resolve("./library.js"), "utf8");

// 从 library.js 中切出 diff 相关逻辑
const start = js.indexOf("  function assistantFieldText(field, value) {");
const end = js.indexOf("  async function openProposalQuestion(", start);
assert.ok(start > -1 && end > start, "切得到 diff 相关源码");

class RealStyleNode {
  constructor(tag, className, text) {
    this.tag = tag;
    this.className = className || "";
    this.textContent = text == null ? "" : String(text);
    this.children = [];
  }
  append(...nodes) {
    this.children.push(...nodes);
    return this;
  }
}

const context = {
  node: (tag, className, text) => new RealStyleNode(tag, className, text),
  document: { createElement: () => {} }, // 模拟真实浏览器环境（包含 createElement）
  ASSISTANT_FIELD_LABELS: { stem: "题干", answer: "答案", options: "选项" },
  QB: { TYPE_NAMES: {} },
};

vm.runInNewContext(js.slice(start, end), context);

// 1. 分词测试
const tokens = context.tokenizeText("若区间 [0, 1) 内，\\alpha = 2");
assert.ok(tokens.includes("["));
assert.ok(tokens.includes(")"));
assert.ok(tokens.includes("\\alpha"));
assert.ok(tokens.includes("2"));

// 2. 差异算法测试：区间从 [0, 1) 改成 (0, 1)
const diff = context.computeTokensDiff("区间 [0, 1) 内", "区间 (0, 1) 内");
const dels = diff.filter((t) => t.type === "del");
const adds = diff.filter((t) => t.type === "add");
const sames = diff.filter((t) => t.type === "same");

assert.equal(dels.length, 1, "应该只识别出一处删除");
assert.equal(dels[0].text, "[", "被删除的是 [");
assert.equal(adds.length, 1, "应该只识别出一处新增");
assert.equal(adds[0].text, "(", "新增的是 (");
assert.ok(sames.some((t) => t.text.includes("区间")), "未变动部分被保留为 same");

// 3. 渲染 DOM 测试：既有删除又有新增（[ 替换为 (）
const row = context.assistantDiffRow("stem", "区间 [0, 1) 内", "区间 (0, 1) 内");
function walk(node, out = []) {
  out.push(node);
  (node.children || []).forEach((c) => walk(c, out));
  return out;
}
const allNodes = walk(row);
const delNodes = allNodes.filter((n) => n.tag === "del" && n.className === "diff-del");
const insNodes = allNodes.filter((n) => n.tag === "ins" && n.className === "diff-add");

assert.ok(delNodes.length > 0, "旧内容中应包含 <del class='diff-del'>");
assert.ok(insNodes.length > 0, "新内容中应包含 <ins class='diff-add'>");
assert.equal(delNodes[0].textContent, "[", "删掉的是 [");
assert.equal(insNodes[0].textContent, "(", "加上的是 (");

console.log("assistant diff tests: OK");
