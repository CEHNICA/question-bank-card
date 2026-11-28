"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const QB = require("./qb-render.js");
global.QBRender = QB;
global.katex = require("./vendor/katex/katex.min.js");
const Export = require("./exam-export.js");

const production = ["| | 优级品 | 合格品 | 不合格品 | 总计 |", "|---|---|---|---|---|", "| 甲车间 | 26 | 24 | 0 | 50 |", "| 乙车间 | 70 | 28 | 2 | 100 |", "| 总计 | 96 | 52 | 2 | 150 |"];
const contingency = ["| | 优级品 | 非优级品 |", "|---|---|---|", "| 甲车间 | | |", "| 乙车间 | | |"];

test("row-spaced question tables render and export without rewriting text or losing empty cells", () => {
  for (const gap of ["\n\n", "\n \t\n\n", "\r\n\r\n"]) {
    const source = `数据如下：\n${production.join(gap)}\n(1) 填写列联表：\n${contingency.join(gap)}\n能否有95%的把握？`;
    const tables = QB.findTables(source);
    assert.equal(tables.length, 2);
    assert.deepEqual(tables[0].rows.map((row) => row.map((cell) => cell.text)), [["", "优级品", "合格品", "不合格品", "总计"], ["甲车间", "26", "24", "0", "50"], ["乙车间", "70", "28", "2", "100"], ["总计", "96", "52", "2", "150"]]);
    assert.deepEqual(tables[1].rows.map((row) => row.map((cell) => cell.text)), [["", "优级品", "非优级品"], ["甲车间", "", ""], ["乙车间", "", ""]]);
    assert.equal(source.slice(tables[0].start, tables[0].end), production.join(gap));
    for (const table of tables) for (const row of table.rows) for (const cell of row) assert.equal(source.slice(cell.start, cell.end), cell.text);
    const exported = Export.serializeField(source);
    assert.equal(exported.source, source);
    assert.deepEqual(exported.blocks.filter((block) => block.type === "table").map((block) => block.rows.map((row) => row.map((cell) => cell.source))), tables.map((table) => table.rows.map((row) => row.map((cell) => cell.text))));
    assert.ok(exported.blocks.some((block) => block.type === "text" && source.slice(block.start, block.end).includes("95%")));
  }
});

test("separate tables, prose, and absolute value expressions stay separate", () => {
  const first = contingency.join("\n\n");
  const second = "| 项目 | 数量 | 备注 |\n\n|---|---|---|\n\n| A | 1 | 有效 |";
  assert.equal(QB.findTables(first + "\n\n" + second).length, 2);
  assert.equal(QB.findTables(first + "\n\n中间说明\n\n" + second).length, 2);
  assert.deepEqual(QB.findTables("|x|=2\n\n|y|=3"), []);
  assert.deepEqual(QB.findTables("| A | B |\n\n| 1 | 2 |"), []);
  const changedColumns = first + "\n\n| 独立 | 两列 |";
  assert.equal(QB.findTables(changedColumns)[0].end, first.length);
});

test("formulas and escaped pipes retain exact source offsets inside spaced table cells", () => {
  const source = "前文\n| 事件 | 数值 |\n\n|---|---|\n\n| $P(|X|>1)$ | $\\frac12$ |\n\n| a\\|b | 0 |\n后文";
  const [table] = QB.findTables(source);
  assert.equal(table.rows[1][0].text, "$P(|X|>1)$");
  assert.equal(table.rows[2][0].text, "a\\|b");
  for (const row of table.rows) for (const cell of row) assert.equal(source.slice(cell.start, cell.end), cell.text);
  const exported = Export.serializeField(source).blocks.find((block) => block.type === "table");
  assert.equal(exported.rows[2][0].source, "a|b");
  assert.ok(exported.rows[1][1].segments.some((segment) => segment.type === "math" && segment.mathml.includes("<mfrac>")));
});
