"use strict";
const assert = require("node:assert/strict");
const QB = require("./qb-render.js");
const workspace = require("./library-workspace.js");

const longFraction = "$\\dfrac{x^2}{(2-y)(2-z)}+\\dfrac{y^2}{(2-z)(2-x)}\\ge3$";
const first = "三元均值不等式：" + "当三个数为正数时，需要保留所有条件，".repeat(5) + longFraction + "。";
const summary = workspace.summaryStem(first + "\n\n（1）求最小值。\n\n（2）证明结论。", QB);
assert.equal(summary.text, first);
assert.equal(summary.folded, true);
assert(summary.text.includes(longFraction));

// Semicolons/newlines inside a matrix or cases block are not prose boundaries.
const matrix = "$\\begin{cases}x=1;\\\\y=\\frac{2}{3}\\end{cases}$";
const onlyFormula = "考虑这个完整条件：" + matrix.repeat(9);
assert.deepEqual(workspace.summaryStem(onlyFormula, QB), { text: onlyFormula, folded: false });
const short = "已知 $a>0$，求 $a+1/a$ 的最小值。";
assert.deepEqual(workspace.summaryStem(short, QB), { text: short, folded: false });

// Keep stable publication identities and ordering when a filtered view changes.
assert.deepEqual(workspace.uniqueIds(["a", "b", "a", null, 3, "c"]), ["a", "b", "c"]);
assert.equal(workspace.uniqueIds(Array.from({ length: 520 }, (_, n) => String(n))).length, 500);
const ids = ["a", "b", "c", "d"];
assert.deepEqual(workspace.moveWithinGroup(ids, "a", "c"), ["c", "b", "a", "d"]);
assert.deepEqual(ids, ["a", "b", "c", "d"]);
assert.deepEqual(workspace.moveWithinGroup(ids, "a", "missing"), ids);
console.log("library workspace invariants: OK");
