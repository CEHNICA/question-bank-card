import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
// @ts-expect-error 前端是原生 JS 模块，没有类型声明
import { escapeHtml, priceLabel, renderMathText, typeName } from "../web/src/render.js";

test("stems escape HTML but still render formulas", () => {
  const html = renderMathText('<img src=x onerror="alert(1)"> 求 $x^2$');
  assert.equal(html.includes("<img"), false);
  assert.ok(html.includes("&lt;img"));
  assert.ok(html.includes('class="katex"'));
});

test("broken TeX does not throw", () => {
  assert.doesNotThrow(() => renderMathText("$\\frac{1$"));
});

test("helpers", () => {
  assert.equal(escapeHtml(`"'&`), "&quot;&#39;&amp;");
  assert.equal(priceLabel(0), "免费");
  assert.equal(priceLabel(2000), "¥20");
  assert.equal(priceLabel(990), "¥9.90");
});

// 题包导出时 publication.question_type 原样带出，社区这边要照着翻成中文。
// 少翻一个键，页面上就会直接露出英文主键（比如 free_response 240）。
test("题型表跟题有据后端 core/qtypes.py 全量对齐", () => {
  const backend = readFileSync(new URL("../../backend/core/qtypes.py", import.meta.url), "utf8");
  const block = backend.split("TYPE_LABELS = {")[1]?.split("}")[0] ?? "";
  const labels = [...block.matchAll(/"([a-z_]+)":\s*"([^"]+)"/g)].map((m) => [m[1], m[2]] as const);
  assert.ok(labels.length >= 6, `后端题型表没解析出来：${block.slice(0, 120)}`);
  for (const [key, label] of labels) {
    assert.equal(typeName(key), label, `题型 ${key} 的中文名跟后端对不上`);
  }
});

test("认不出的题型不把英文主键甩到页面上", () => {
  assert.equal(typeName("free_response"), "解答题");
  assert.equal(typeName("unknown"), "题型未定");
  assert.equal(typeName("fill_blank"), "填空题");
  assert.equal(typeName("true_false"), "判断题");
  assert.equal(typeName("some_future_type"), "其他");
  assert.equal(typeName(""), "其他");
  assert.equal(typeName(undefined), "其他");
});
