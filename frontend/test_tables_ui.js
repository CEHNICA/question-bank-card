"use strict";

// Tables that are only words and numbers are question text: they render as
// real tables, can be edited as text, and a table crop can become one.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const QB = require("./qb-render.js");
const { insertTableText, growTableText } = require("./app.js");

const table = "| | 优级品 | 非优级品 |\n|---|---|---|\n| 甲车间 | | |\n| 乙车间 | $|x|$ | a\\|b |";
const text = `（1）填写如下列联表：\n${table}\n能否认为有差异？`;
const [found] = QB.findTables(text);
assert.equal(found.header, true);
assert.deepEqual(found.rows.map((row) => row.map((cell) => cell.text)),
  [["", "优级品", "非优级品"], ["甲车间", "", ""], ["乙车间", "$|x|$", "a\\|b"]]);
// Cell positions point into the text, so disputed-character marks land in the right cell.
const cell = found.rows[0][1];
assert.equal(text.slice(cell.start, cell.end), "优级品");
assert.equal(text.slice(found.start, found.end), table);

// |x|=2 on one line is maths, not a table.
assert.deepEqual(QB.findTables("若 |x|=2，求 x"), []);
assert.deepEqual(QB.findTables("|x|=2\n且 |y|=3"), []);

// Merged cells come as a small HTML table; entities are decoded.
const [html] = QB.findTables('见下表\n<table><tr><td rowspan="2">A&#x27;</td><td>1</td></tr><tr><td>2</td></tr></table>');
assert.equal(html.rows[0][0].text, "A'");
assert.equal(html.rows[0][0].rowspan, 2);

// Editing helpers: insert a table on its own lines, grow the one under the cursor.
const inserted = insertTableText("数据如下：", 5);
assert.equal(inserted.value, "数据如下：\n|  |  |  |\n|---|---|---|\n|  |  |  |\n|  |  |  |");
const moreRows = growTableText(inserted.value, inserted.cursor, "row");
assert.equal(moreRows.value.split("\n").length, 6);
const moreCols = growTableText(inserted.value, inserted.cursor, "col");
assert.match(moreCols.value, /\|---\|---\|---\|---\|/);
assert.equal(QB.findTables(moreCols.value)[0].rows[0].length, 4);
assert.equal(growTableText("没有表格", 2, "row"), null);

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const render = fs.readFileSync(path.join(__dirname, "qb-render.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
// The typeset view lays tables out and keeps the rest of the text as before.
assert.match(render, /function renderTypeset\(node, value, options = \{\}\) \{[\s\S]*?findTables\(source\)[\s\S]*?renderTable\(doc, table, marks, options\)/);
assert.match(render, /figureElement\(figure, opts\.resolveUrl, opts\.figureAction\)/);
assert.match(css, /\.qb-table td, \.qb-table th \{[^}]*border: 1px solid var\(--paper-ink\)/);
// A crop MinerU read as a table offers the conversion on the card.
assert.match(js, /figureAction: \(figure\) => tableAction\(q, figure\)/);
assert.match(js, /api\(`\/api\/questions\/\$\{q\.id\}\/figure-table`, \{ method: "POST", body: \{ figure: index \} \}\)/);
assert.match(js, /button\("插入表格", "small"/);
// A collapsed card shows the first line of words, not a table row.
assert.match(js, /lines\.find\(\(item\) => item\.trim\(\) && !item\.trim\(\)\.startsWith\("\|"\)/);

console.log("table UI checks: OK");
