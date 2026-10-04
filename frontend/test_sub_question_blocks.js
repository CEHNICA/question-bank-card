"use strict";

// Sub-question splitting is display only. These cases pin the boundaries that
// matter: a marker inside a formula, a coordinate, a matrix row and a
// non-ascending number must all leave the text as one block.

const assert = require("node:assert/strict");
const QB = require("./qb-render.js");

function labels(value) { return QB.subQuestionParts(value).map(part => part.label); }
function bodies(value) { return QB.subQuestionParts(value).map(part => part.body); }

// Ordinary three-part answer splits into three blocks, and the separators
// between them are not left dangling at either end.
const plain = "(1) 甲；(2) 乙；(3) 丙。";
assert.deepEqual(labels(plain), ["(1)", "(2)", "(3)"]);
assert.deepEqual(bodies(plain), ["甲", "乙", "丙。"]);

// A marker inside a formula is not a sub-question, and the formula keeps its
// own parentheses untouched in the text that precedes the first marker.
const inFormula = "值 $(1,2)$ 满足 (1) 甲；(2) 乙。";
const parts = QB.subQuestionParts(inFormula);
assert.deepEqual(parts.map(part => part.label), ["(1)", "(2)"]);
assert.equal(parts[0].lead, "值 $(1,2)$ 满足 ", "the coordinate stayed outside every sub-question body");
assert.ok(inFormula.includes("$(1,2)$"), "the original string keeps the coordinate");

// Nothing outside a formula is a sub-question marker.
assert.deepEqual(labels("点 $(1)$ 和 $(2)$。"), []);

// Full-width brackets are accepted too.
assert.deepEqual(labels("（1）甲；（2）乙。"), ["（1）", "（2）"]);

// A matrix row such as (1) inside a display formula must not split.
const matrix = "$\\begin{pmatrix} 1 & 2 \\\\ 2 & 1 \\end{pmatrix}$ (1) 甲；(2) 乙。";
assert.deepEqual(labels(matrix), ["(1)", "(2)"]);

// A run that does not start at 1 is not a sub-question list: "答案 (2)...(3)"
// is a continuation, not a new block structure.
assert.deepEqual(labels("答案 (2) 乙；(3) 丙。"), []);

// A gap in the numbering means this is not a clean sub-question list, so the
// text stays one block. Inventing structure from a mis-typed number is worse
// than leaving the line as it is.
assert.deepEqual(labels("(1) 甲；(3) 丙；(4) 丁。"), []);

// A single marker is not a list.
assert.deepEqual(labels("只有 (1) 一个。"), []);

// A leading fragment before the first marker is kept as that part's lead-in,
// never dropped and never merged into a body.
const lead = "解：(1) 甲；(2) 乙。";
const leadParts = QB.subQuestionParts(lead);
assert.deepEqual(leadParts.map(part => part.body), ["甲", "乙。"]);
assert.equal(leadParts[0].lead, "解：");
assert.equal(leadParts[1].lead, "", "only the first part carries a lead-in");


// Every part is a real slice of the source read from its own offset, and the
// blocks advance in order, so rendering can never drop or reorder a character.
for (const value of [plain, inFormula, lead, matrix, "（1）甲\n\n（2）乙。"]) {
  const parts = QB.subQuestionParts(value);
  if (!parts.length) continue;
  let cursor = 0;
  for (const part of parts) {
    assert.ok(part.offset >= cursor, `blocks advance through the source for ${value}`);
    assert.equal(value.slice(part.offset, part.offset + part.body.length), part.body,
      `the body is read from its own offset in the source for ${value}`);
    const labelAt = value.lastIndexOf(part.label, part.offset);
    assert.ok(labelAt >= 0 && value.slice(labelAt + part.label.length, part.offset).match(/^[\s　；;，,、。.：:]*$/),
      `the label sits just before its body, separated only by punctuation, for ${value}`);
    if (part.lead) assert.equal(value.slice(0, labelAt), part.lead, "the lead-in is the real text before the first label");
    cursor = part.offset + part.body.length;
  }
}

// Empty, blank and very long inputs stay a single block rather than splitting.
assert.deepEqual(labels(""), []);
assert.deepEqual(labels("   \n  "), []);
assert.deepEqual(labels("(1) 甲".repeat(4000)), []);

console.log("Sub-question display blocks: marker-in-formula, coordinate, matrix, numbering and source coverage OK");
