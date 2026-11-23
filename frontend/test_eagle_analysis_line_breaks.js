"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const QB = require("./qb-render.js");

const INDEX_HTML_PATH = path.join(__dirname, "eagle", "index.html");

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.nodeValue = "";
    this.textContent = "";
    this.classList = {
      add: (...names) => {
        const current = new Set(this.className.split(/\s+/).filter(Boolean));
        names.forEach((n) => current.add(n));
        this.className = Array.from(current).join(" ");
      },
      remove: (...names) => {
        const removeSet = new Set(names);
        this.className = this.className
          .split(/\s+/)
          .filter((n) => n && !removeSet.has(n))
          .join(" ");
      },
    };
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  querySelectorAll() {
    return [];
  }
}

function collectText(node) {
  if (!node) return "";
  if (node.tagName === "BR") return "\n";
  if (node.className && node.className.split(" ").includes("qb-math")) {
    return node.dataset.raw || "";
  }
  if (node.children && node.children.length) {
    return node.children.map(collectText).join("");
  }
  return node.tagName ? node.textContent || "" : node.nodeValue || "";
}

test("Eagle 列表卡片与快速审题解析正文均保留换行并转义内容", () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, "utf8");

  assert.match(
    html,
    /\.eagle-analysis-body\s*\{[^}]*white-space:\s*pre-line;[^}]*overflow-wrap:\s*anywhere;/,
    ".eagle-analysis-body 必须显式启用 white-space: pre-line 与 overflow-wrap: anywhere"
  );
  assert.match(
    html,
    /#quickLookModal\s+\.ql-sol-content\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/,
    "#quickLookModal .ql-sol-content 必须显式启用 white-space: pre-wrap"
  );

  const scriptMatches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.ok(scriptMatches.length > 0, "eagle/index.html 应包含内联脚本");
  const mainScript = scriptMatches[scriptMatches.length - 1][1];
  assert.doesNotThrow(() => new vm.Script(mainScript), "内联脚本应无语法错误");

  const escapeMatch = mainScript.match(/function escapeHtml\(str\)\s*\{[\s\S]*?\n {4}\}/);
  const analysisMatch = mainScript.match(/function renderAnalysisContentHtml\(q\)\s*\{[\s\S]*?\n {4}\}/);
  const aiResultMatch = mainScript.match(/function renderAiResultHtml\(q,\s*errs\)\s*\{[\s\S]*?\n {4}\}/);
  assert.ok(escapeMatch && analysisMatch && aiResultMatch, "应能提取 escapeHtml、renderAnalysisContentHtml 与 renderAiResultHtml");

  const ctx = {};
  vm.runInNewContext(`${escapeMatch[0]}\n${analysisMatch[0]}\n${aiResultMatch[0]}`, ctx);

  const sample = "\n思路 第一步。\n解答 取 $x=1$ 且 <script>bad</script>。\n反思 结论成立。\n";
  const cases = [
    ctx.renderAnalysisContentHtml({ id: "q1", savedSolution: { analysis: sample } }),
    ctx.renderAnalysisContentHtml({ id: "q2", rawAnalysis: sample }),
    ctx.renderAnalysisContentHtml({ id: "q3", aiAnswer: { engine: "TestAI", analysis: sample } }),
    ctx.renderAiResultHtml({ id: "q4", aiAnswer: { engine: "TestAI", analysis: sample } }, []),
  ];

  for (const rendered of cases) {
    const bodyMatch = rendered.match(/<div class="eagle-analysis-body[^"]*">([\s\S]*?)<\/div>/);
    assert.ok(bodyMatch, `解析正文容器必须带 eagle-analysis-body 类: ${rendered}`);
    const inner = bodyMatch[1];
    assert.ok(!inner.startsWith("\n") && !inner.endsWith("\n"), "容器内首尾不应留多余空行");
    assert.ok(inner.includes("\n解答 取 $x=1$ 且 &lt;script&gt;bad&lt;/script&gt;。\n反思 结论成立。"), `应保留内部分段换行并转义尖括号: ${inner}`);
    assert.ok(!inner.includes("<script>"), "不得保留未转义 HTML 标签");
  }
});

test("QB.tidyText 与 renderTypeset 保留教辅栏目起首换行且不误拆普通软换行", () => {
  const prevDoc = global.document;
  const prevKatex = global.katex;
  global.document = {
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => ({ nodeType: 3, nodeValue: text }),
  };
  global.katex = {
    render: (latex, node) => {
      node.innerHTML = `katex:${latex}`;
    },
  };

  try {
    const structured = "思路 先取特殊点检验。\n解答 取 $x=0$ 得结果。\n反思 正面推导也成立。";
    const tidied = QB.tidyText(structured);
    assert.deepEqual(tidied.split("\n"), [
      "思路先取特殊点检验。",
      "解答取$x=0$得结果。",
      "反思正面推导也成立。",
    ]);

    const methods = "解法一：直接代入。\n解法二：数形结合。\n方法二：先化简式子。";
    assert.deepEqual(QB.tidyText(methods).split("\n"), [
      "解法一：直接代入。",
      "解法二：数形结合。",
      "方法二：先化简式子。",
    ]);

    const host = global.document.createElement("div");
    QB.renderTypeset(host, structured);
    const rendered = collectText(host);
    assert.deepEqual(rendered.split("\n"), [
      "思路先取特殊点检验。",
      "解答取$x=0$得结果。",
      "反思正面推导也成立。",
    ]);
    assert.equal(rendered.replace(/\s+/g, ""), structured.replace(/\s+/g, ""), "不得增删非空白字符");

    // 普通题干里的软换行（非栏目起首）仍应合并，不能被误判为栏目换行
    assert.equal(QB.tidyText("请按上述\n思路继续计算"), "请按上述思路继续计算");
    assert.equal(QB.tidyText("已知线段长\n度为 2"), "已知线段长度为2");
  } finally {
    global.document = prevDoc;
    global.katex = prevKatex;
  }
});
