"use strict";

// 详情页链接：只留一个链接图标打开；点输入框先全选，再点取消全选。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

assert.match(html, /id="inspectorUrlOpenBtn"/);
assert.match(html, /aria-label="打开链接"/);
assert.match(html, /function openInspectorUrl\(/);
assert.match(html, /function handleInspectorUrlFocus\(/);
assert.match(html, /function handleInspectorUrlMouseDown\(/);
assert.match(html, /onfocus="handleInspectorUrlFocus\(event\)"/);
assert.match(html, /onmousedown="handleInspectorUrlMouseDown\(event\)"/);
assert.doesNotMatch(html, />开<\/button>/);
assert.doesNotMatch(html, /onclick="copyInspectorUrl\(\)"/);
assert.doesNotMatch(html, /function copyInspectorUrl\(/);

console.log("Eagle 详情页链接图标与全选交互：OK");
