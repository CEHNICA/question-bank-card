"use strict";
// Eagle 标签区：安静标题 + 胶囊 + 末尾「+」；点 + / 按 T 才展开输入
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("Eagle 标签添加界面结构", () => {
  assert.doesNotMatch(html, /按 T 快速打标/);
  assert.doesNotMatch(html, /eagle-tag-input-kbd/);
  assert.doesNotMatch(html, /暂无标签<\/span>`/); // 属性栏空态不再写这句话
  assert.match(html, /class="eagle-tag-add-btn/);
  assert.match(html, /function openTagCompose\(/);
  assert.match(html, /function closeTagCompose\(/);
  assert.match(html, /id="tagAutocompleteWrap" class="hidden relative"/);
  assert.match(html, /placeholder="搜索或创建标签…"/);
  assert.match(html, /onclick="openTagCompose\(\)"/);
  assert.match(html, /<div class="text-\[11px\] font-medium text-slate-500">标签<\/div>/);
});
