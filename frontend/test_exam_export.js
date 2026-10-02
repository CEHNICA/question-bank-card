"use strict";
const assert = require("node:assert/strict");
global.QBRender = require("./qb-render.js");
global.katex = require("./vendor/katex/katex.min.js");
const Export = require("./exam-export.js");

function checkCoverage(field) {
  let cursor = 0;
  for (const block of field.blocks) {
    assert.equal(block.start, cursor);
    if (block.type === "text") {
      let previous = block.start;
      for (const segment of block.segments) { assert.equal(segment.start, previous); previous = segment.end; }
      assert.equal(previous, block.end);
    } else for (const row of block.rows) for (const cell of row) {
      checkCoverage({ source: cell.source, blocks: cell.source ? [{ type: "text", start: 0, end: cell.source.length, segments: cell.segments }] : [] });
    }
    cursor = block.end;
  }
  assert.equal(cursor, field.source.length);
}
for (const source of ["", "  ", "求 $\\frac{1}{x^2}+\\sqrt{x+1}$ 的值。", "🧮已知 x>0，求 x^2+1。", "$▱ABCD$", "\\(▱ABCD\\)", "$$\\text{▱}ABCD$$", "填空 ____ ，选择（　）。", "证明 $a\\parallel b$，$a\\nparallel c$。", "| 概率 | $P(A)$ |\n|---|---|\n| $x$ | $\\frac12$ |", "<table><tr><th rowspan='2'>频数</th><td>$x^2$</td></tr><tr><td>3 &amp; 4</td></tr></table>"]) {
  checkCoverage(Export.serializeField(source));
}
const field = Export.serializeField("求 $\\frac{a+b}{3}\\geq\\sqrt[3]{abc}$。\n(1) 当 x>0 时，求 y=2x+1/x^3 最小值。");
assert(field.blocks[0].segments.some((segment) => segment.mathml?.includes("<mfrac>")));
assert(field.blocks[0].segments.some((segment) => segment.mathml?.includes("<mroot>")));
assert(field.blocks[0].segments.filter((segment) => segment.type === "math").every((segment) => segment.mathml.includes('encoding="application/x-tex"')));
const parallel = Export.serializeField("$a\\parallel b$");
assert(parallel.blocks[0].segments[0].mathml.includes("∥"), "Word uses the standard native parallel symbol rather than negative-space slash macros");
assert.throws(() => Export.serializeField("$\\unsupportedcommand{x}$"), /Undefined control sequence/);
const merged = Export.serializeField("<table><tr><th rowspan='2'>频数</th><td>$x^2$</td></tr><tr><td>3 &amp; 4</td></tr></table>").blocks[0];
assert.equal(merged.type, "table"); assert.equal(merged.rows[0][0].rowspan, 2); assert.equal(merged.rows[1][0].source, "3 & 4");
const item = { id: "publication-1", number: 19, origin: "期中", content: { stem: "$x^2$", options: { A: "$\\frac12$" }, answer: "AC", analysis: "原卷解析" }, ai_answer: { answer: "B", analysis: "AI解答" } };
const all = Export.serializeFields([item], { document: "combined", ai_answers: true, origin: true });
assert.equal(all[item.id].answer.blocks[0].segments[0].type, "text", "choice answers stay upright text");
assert.equal(all[item.id].analysis.source, "原卷解析", "an AI answer cannot replace an original answer");
assert.equal(all[item.id].origin.source, "期中");
const questions = Export.serializeFields([item], { document: "questions" });
assert(!questions[item.id].answer && !questions[item.id].analysis);
const answers = Export.serializeFields([item], { document: "answers" });
assert(!answers[item.id].stem && !answers[item.id].origin);
const split = Export.serializeFields([item], { document: "questions" }, "split");
assert(split[item.id].answer && split[item.id].stem);
const withoutOriginal = { ...item, content: { stem: "题干", answer: "", analysis: "" } };
assert.equal(Export.selectedAnswer(withoutOriginal, { ai_answers: false }), null);
assert.equal(Export.selectedAnswer(withoutOriginal, { ai_answers: true }).ai, true);
assert.throws(() => Export.serializeFields([item, item], { document: "questions" }), /编号.*重复/);
assert.equal(Export.fileName("attachment; filename*=UTF-8''%E6%95%B0%E5%AD%A6.docx", "fallback.docx"), "数学.docx");
assert.equal(Export.fileName('attachment; filename="CON.docx"', "fallback.docx"), "试卷-CON.docx");
assert.equal(Export.fileName('attachment; filename="../bad:paper.docx"', "fallback.docx"), ".._bad_paper.docx");
console.log("Word export source coverage, editable formulas, tables, answer separation and safe filenames: OK");

(async () => {
  const realTimeout = global.setTimeout;
  const timers = [], links = [], requests = [], revoked = [];
  global.setTimeout = (callback, delay) => delay === 60000 ? timers.push(callback) : realTimeout(callback, delay);
  global.document = { body: { append: link => links.push(link) }, createElement: () => ({
    click() { this.clicked = true; }, remove() { this.removed = true; }
  }) };
  global.URL.createObjectURL = () => "blob:test-local-export";
  global.URL.revokeObjectURL = value => revoked.push(value);
  const mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  const response = (changes = {}, data = new Uint8Array([80, 75, 3, 4, 0])) => new Response(data, { headers: {
    "Content-Type": mime, "X-Question-Count": "1", "Content-Disposition": "attachment; filename*=UTF-8''%E6%95%B0%E5%AD%A6.docx", ...changes
  } });
  global.fetch = async (url, request) => { requests.push({ url, request }); return response(); };
  const result = await Export.download([item], { title: "数学", print_options: { document: "questions" } });
  assert.deepEqual(result, { filename: "数学.docx", question_count: 1 });
  assert.equal(requests[0].url, "/api/library/export-docx");
  assert.equal(requests[0].request.headers["X-QB-Request"], "1");
  assert(!JSON.parse(requests[0].request.body).rendered_fields[item.id].answer);
  assert.equal(links.length, 1); assert(links[0].clicked && links[0].removed);
  timers.shift()(); assert.deepEqual(revoked, ["blob:test-local-export"]);

  // A server error, wrong count, format or incomplete archive never starts a download.
  for (const [reply, message] of [
    [new Response(JSON.stringify({ error: "第 19 题配图缺失" }), { status: 409, headers: { "Content-Type": "application/json" } }), /配图缺失/],
    [response({ "X-Question-Count": "0" }), /题目数量/],
    [response({ "Content-Type": "text/html" }), /导出文件/],
    [response({}, new Uint8Array([1, 2, 3, 4])), /不完整/]
  ]) {
    global.fetch = async () => reply;
    await assert.rejects(Export.download([item], { print_options: { document: "questions" } }), message);
    assert.equal(links.length, 1);
  }
  await assert.rejects(Export.download([null], { print_options: { document: "questions" } }), /编号不完整/);
  await assert.rejects(Export.download([withoutOriginal], { format: "split", print_options: { document: "questions" } }), /没有可附/);

  // A second click is blocked while the original immutable request is in flight.
  let resolveReply;
  global.fetch = () => new Promise(resolve => { resolveReply = resolve; });
  const first = Export.download([item], { print_options: { document: "questions" } });
  while (!resolveReply) await new Promise(resolve => realTimeout(resolve, 1));
  await assert.rejects(Export.download([item], { print_options: { document: "questions" } }), /正在导出/);
  resolveReply(response()); await first;
  assert.equal(links.length, 2);
  console.log("Word download transport, failure containment and repeated-click protection: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
