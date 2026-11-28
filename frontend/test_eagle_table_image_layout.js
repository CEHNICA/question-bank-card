"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const QB = require("./qb-render.js");
const html = fs.readFileSync(process.env.EAGLE_LAYOUT_HTML || path.join(__dirname, "eagle/index.html"), "utf8");

function functions() {
  return ["escapeHtml", "formatEagleStemBlocksHtml", "renderAdaptiveOptionsHtml"].map((name) => {
    const match = html.match(new RegExp(`function ${name}\\([^]*?\\n {4}\\}`));
    assert.ok(match, name);
    return match[0];
  }).join("\n");
}

test("Eagle list stem sends whole Markdown tables to the shared renderer", () => {
  const calls = [];
  const scope = { window: { QBRender: { findTables: QB.findTables, renderTypeset: (host, source) => {
    calls.push(source); host.innerHTML = `<table data-rows="${QB.findTables(source)[0].rows.length}"></table>`;
  } } }, document: { createElement: () => ({ innerHTML: "" }) } };
  vm.runInNewContext(functions(), scope);
  const table = "| | 优级品 | 非优级品 |\n\n|---|---|---|\n\n| 甲车间 | | |\n\n| 乙车间 | | |";
  const source = `正文 <script>bad</script>\n${table}\n(1) 能否认为有差异？\n$$\nx^2+y^2=1\n$$`;
  const result = scope.formatEagleStemBlocksHtml(source);
  assert.deepEqual(calls, [table]);
  assert.ok(result.includes('<table data-rows="3"></table>'));
  assert.ok(result.includes("&lt;script&gt;bad&lt;/script&gt;"));
  assert.ok(result.includes("(1) 能否认为有差异？"));
  assert.match(result, /eagle-stem-math/);
  assert.ok(!result.includes("<script>bad"));
});

test("image options retain labels, text and original URLs in accessible preview buttons", () => {
  const scope = { API_BASE: "" };
  vm.runInNewContext(functions(), scope);
  const options = { A: "俯视图", B: "", C: "", D: "" };
  const figures = Object.fromEntries(["A", "B", "C", "D"].map((key) => [key, { url: `/fixture/${key}.png?note="test"` }]));
  const before = JSON.stringify({ options, figures });
  const result = scope.renderAdaptiveOptionsHtml(options, figures, 8);
  assert.equal((result.match(/data-eagle-option-preview/g) || []).length, 4);
  assert.equal((result.match(/class="eagle-option-image"/g) || []).length, 4);
  assert.ok(result.includes('aria-label="放大查看选项 A 配图"'));
  assert.ok(result.includes("俯视图"));
  assert.ok(result.includes('/fixture/A.png?note=&quot;test&quot;'));
  assert.equal(JSON.stringify({ options, figures }), before);
});
