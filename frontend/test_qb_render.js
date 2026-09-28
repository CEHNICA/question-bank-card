"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const QB = require("./qb-render.js");

// 本地规则补出的首题必须在题卡旁直接提示人工核对；范围人工调整
// 仍具有更高显示优先级，不能只把说明藏到任务设置里。
const appSource = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
assert.match(appSource, /q\.regions_changed \?[^:]+:\s*q\.start_source === "inferred"/s);
assert.match(appSource, /题号由本地规则补出，请对照原卷核对 · 点击放大对照/);

// 平行四边形符号必须成为独立的 Unicode 显示片段，不能送进 KaTeX 的
// \\square。后面的顶点字母仍然作为数学片段排版。
const plain = QB.typesetSegments("在▱ABCD中");
assert.equal(plain.filter((part) => part.type === "parallelogram").length, 1);
assert.equal(plain.find((part) => part.type === "parallelogram").start, 1);
assert.equal(plain.find((part) => part.type === "math" && part.start === 2).latex, "ABCD");
assert.ok(!plain.some((part) => String(part.latex || "").includes("\\square")));

// 显式公式中的 Unicode ▱ 也要保留语义。
const explicit = QB.typesetSegments("$▱ABCD$");
assert.deepEqual(explicit.map((part) => part.type), ["parallelogram", "math"]);
assert.equal(explicit[1].latex, "ABCD");

const explicitText = QB.typesetSegments("$\\text{▱}ABCD$");
assert.deepEqual(explicitText.map((part) => part.type), ["parallelogram", "math"]);
assert.equal(explicitText[1].latex, "ABCD");

// 后端可能给符号套 text/mathrm/mathbf；展示公式拆分后仍应属于同一个
// 横向居中组，不能变成四个竖排块。
const display = QB.typesetSegments("$$\\text{▱}ABCD \\cong \\mathbf{▱}EFGH$$");
assert.deepEqual(display.map((part) => part.type), ["parallelogram", "math", "parallelogram", "math"]);
assert.ok(display[0].displayGroup);
assert.ok(display.every((part) => part.displayGroup === display[0].displayGroup));
assert.ok(display.every((part) => part.display === false));
assert.equal(display[1].latex, "ABCD \\cong ");
assert.equal(display[3].latex, "EFGH");

// 真正的方框 / LaTeX \\square 不能被误改成平行四边形。
assert.equal(QB.runToLatex("□"), "\\square ");
const square = QB.typesetSegments("$\\square$");
assert.equal(square.length, 1);
assert.equal(square[0].type, "math");
assert.equal(square[0].latex, "\\square");
assert.equal(square.some((part) => part.type === "parallelogram"), false);

// ▱ 与 □ 不是同义符号；比对时必须作为实质差异处理。
assert.equal(QB.compareTexts("▱ABCD", "□ABCD").level, "content");

// 放大对照默认必须同时适合宽度和高度；固定高度的跨页连接条也要计入。
assert.equal(QB.fitScale((scale) => ({ width: 900 * scale, height: 600 * scale }), 900, 700), 1);
const tallFit = QB.fitScale((scale) => ({ width: 900 * scale, height: 1400 * scale }), 900, 700);
assert.ok(tallFit > 0.49 && tallFit <= 0.501);
const joinedFit = QB.fitScale((scale) => ({ width: 900 * scale, height: 40 + 1200 * scale }), 900, 640);
assert.ok(joinedFit > 0.49 && joinedFit <= 0.501);
assert.equal(QB.fitScale(() => ({ width: 900, height: 9000 }), 900, 100, { min: 0.04 }), 0.04);

console.log("qb-render regression checks: OK");
