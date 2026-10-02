"use strict";
const assert = require("node:assert/strict");
const { recommendedPrefill } = require("./app.js");

const q = { stem: "已知😀，求 $x^3$ 的值。", options: { A: "$1$", B: "" } };
const start = q.stem.indexOf("$x^3$");
const r = { status: "done", target: "auto", text: "$x^2$", recommendation: {
  status: "recommended", field: "stem", start, end: start + 5,
  before: "$x^3$", after: "$x^2$", field_text: q.stem
} };
assert.deepEqual(recommendedPrefill(q, r), { field: "stem", value: "$x^2$", start, end: start + 5, before: "$x^3$" });
assert.equal(q.stem, "已知😀，求 $x^3$ 的值。", "Preparing a suggestion must not mutate a question");
for (const status of ["manual", "stale", "pending"]) {
  assert.equal(recommendedPrefill(q, { ...r, recommendation: { ...r.recommendation, status } }), null);
}
assert.equal(recommendedPrefill({ ...q, stem: q.stem.replace("已知", "现在") }, r), null, "Changed context cannot reuse old offsets");
assert.equal(recommendedPrefill(q, { ...r, text: "$x^4$" }), null, "The suggested replacement must be this read result");
for (const change of [{ start: -1 }, { end: 999 }, { start: 3.5 }, { field: "F" }, { before: "$x^2$" }]) {
  assert.equal(recommendedPrefill(q, { ...r, recommendation: { ...r.recommendation, ...change } }), null);
}
const repeated = "求 $x^3$，再求 $x^3$。";
assert.equal(recommendedPrefill({ ...q, stem: repeated }, { ...r, recommendation: { ...r.recommendation,
  field_text: repeated, start: 2, end: 7 } }), null, "Ambiguous repeated text needs a manual choice");
const emojiHalf = { ...r, text: "字", recommendation: { ...r.recommendation, start: 3, end: 4,
  before: q.stem.slice(3, 4), after: "字" } };
assert.equal(recommendedPrefill(q, emojiHalf), null, "A UTF-16 offset cannot split one visible character");
const emptyOption = { ...r, text: "$2$", recommendation: { status: "recommended", field: "B", start: 0, end: 0,
  before: "", after: "$2$", field_text: "" } };
assert.deepEqual(recommendedPrefill(q, emptyOption), { field: "B", value: "$2$", start: 0, end: 0, before: "" });
assert.equal(recommendedPrefill(q, { ...emptyOption, recommendation: { ...emptyOption.recommendation, field: "A" } }), null);
assert.equal(recommendedPrefill({ stem: "" }, { ...emptyOption, recommendation: { ...emptyOption.recommendation, field: "stem" } }), null);
console.log("region recommendation validation checks: OK");
