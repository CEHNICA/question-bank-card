"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const App = require("./app.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

const report = App.normalizeReport({
  read_only: true,
  model_calls: 0,
  summary: {
    kept: 12, added: 3, locally_trimmed: 5, range_changed: 2, suspected_excluded: 1,
    protected_unmatched: 4, too_long: 2,
  },
  items: {
    added: [{ number: 6, group: "第二章", pages: [18], reason: "新规则找到的新题卡" }],
  },
  notes: ["只读检查"],
});
assert.equal(report.readOnly, true);
assert.equal(report.modelCalls, 0);
assert.deepEqual(report.categories.map((item) => [item.key, item.count]), [
  ["kept", 12], ["added", 3], ["locally_trimmed", 5], ["range_changed", 2], ["suspected_excluded", 1],
  ["protected_unmatched", 4], ["too_long", 2],
]);
assert.equal(App.itemTitle(report.categories[1].items[0]), "第二章 · 第 6 题 · 第 18 页");

for (const id of [
  "resegmentPreviewDialog", "resegmentPreviewState", "resegmentPreviewSummary",
  "resegmentPreviewDetails", "resegmentAcknowledge", "resegmentApply", "resegmentPreviewResult",
]) assert.match(html, new RegExp(`id="${id}"`));

for (const label of ["原样保留", "新增题卡", "例题去解", "范围变化", "系统移入回收站", "受保护", "异常超长"]) {
  assert.match(js, new RegExp(label));
}

const flow = js.match(/async function resegmentPaper\(\)[\s\S]*?\n  \$\("resegment"\)\.addEventListener/)?.[0] || "";
assert.match(flow, /\/resegment\/preview`[^]*method:\s*"POST"/);
assert.doesNotMatch(flow, /confirmDialog/);
assert.match(js, /if \(!resegmentPreview\.report \|\| !\$\("resegmentAcknowledge"\)\.checked/);
assert.match(js, /\/resegment`, \{ method: "POST", body: \{\} \}/);
assert.ok(js.indexOf("/resegment/preview`") < js.lastIndexOf("/resegment`"));
assert.match(js, /现在不能重新切题：\$\{error\.message \|\| error\}/);
assert.match(js, /未能应用：\$\{error\.message\}/);

assert.match(css, /\.resegment-preview-dialog\s*\{[^}]*width:\s*min\(820px, 94vw\)/s);
assert.match(css, /@media \(max-width: 760px\)[\s\S]*\.resegment-preview-dialog\s*\{[^}]*width:\s*100vw[^}]*height:\s*100dvh/s);
assert.match(css, /\.resegment-preview-scroll\s*\{[^}]*overflow:\s*auto/s);

console.log("resegment preview UI regression checks: OK");
