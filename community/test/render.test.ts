import { test } from "node:test";
import assert from "node:assert/strict";
// @ts-expect-error 前端是原生 JS 模块，没有类型声明
import { escapeHtml, priceLabel, renderMathText } from "../web/src/render.js";

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
