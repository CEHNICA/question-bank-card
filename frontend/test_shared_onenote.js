"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { figuresHtml } = require("./shared-onenote.js");

test("作者笔记与解析图片按顺序显示，只接受本机解析图片接口", () => {
  const prefix = "/api/library/11111111-1111-4111-8111-111111111111/solution-images/";
  const first = prefix + "11111111-1111-4111-8111-111111111112?v=abcdef";
  const second = prefix + "11111111-1111-4111-8111-111111111113";
  const html = figuresHtml({ figures: [{ url: first }, { url: "javascript:alert(1)" }, { url: second }, { url: "https://example.invalid/private.png" }] });
  assert.equal((html.match(/<img /g) || []).length, 2);
  assert.ok(html.indexOf(first) < html.indexOf(second));
  assert.ok(!html.includes("javascript:"));
  assert.ok(!html.includes("example.invalid"));
});
