"use strict";
// 展开左栏文件夹时，若内容落到视口外，应自动滑到眼前。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

describe("左栏展开文件夹自动滚入视口", () => {
  it("1. toggle 展开后会调用 ensureExpandedFolderInView", () => {
    assert.match(html, /function ensureExpandedFolderInView\(/);
    assert.match(html, /function toggleFolderNode\(/);
    assert.match(html, /willExpand/);
    assert.match(html, /ensureExpandedFolderInView\(nodeId\)/);
    assert.match(html, /data-ftree-kids="/);
    assert.match(html, /id="ftree-\$\{escapeHtml\(node\.id\)\}"/);
  });

  it("2. 内容溢出底部时只滚必要距离，超高时钉住标题", () => {
    const match = html.match(/function ensureExpandedFolderInView\(nodeId\) \{[\s\S]*?\n    function toggleFolderNode/);
    assert.ok(match, "应能抽出 ensureExpandedFolderInView");
    const body = match[0];
    assert.match(body, /blockHeight > cRect\.height/);
    assert.match(body, /scrollBy\(\{ top: delta, behavior: 'smooth' \}\)/);
    assert.match(body, /子项比视口还高/);
  });

  it("3. 展开后才滚动，收起不抢滚动", () => {
    const match = html.match(/function toggleFolderNode\(nodeId\) \{[\s\S]*?\n    function handleTreeSearch/);
    assert.ok(match);
    assert.match(match[0], /if \(willExpand\)/);
    assert.doesNotMatch(match[0], /ensureExpandedFolderInView\(nodeId\);\s*\n\s*writeExpanded/);
  });
});
