// 第四十四轮：表格跨页时，后一页要把列名再印一遍。
//
// 实测出来的真缺陷（真 PDF，Chrome printToPDF 出的纸）：
//   40 行 4 列的表跨 2 页，第 2 页一个列名都没有 —— 只剩一串数字，
//   读者根本不知道那一列是什么。
//
// 根因：exam-layout.js 里补表头的那段找的是 <thead>，可渲染出来的表格
// 压根没有 thead（首行只是 th，整段等于死代码）。
//
// 修法：qb-render.js 给「整行都是表头格」的首行打上 qb-table-head-row，
// exam-layout.js 续页时把这行克隆一份放到片段表格的最上面。
//
// 用 QB_RENDER_JS / QB_EXAM_LAYOUT_JS 喂旧版本可以自证看门狗有效。

const path = require("node:path");
const fs = require("node:fs");
const test = require("node:test");
const assert = require("node:assert/strict");

const ROOT = path.resolve(__dirname, "..");
function read(envName, rel) {
  return fs.readFileSync(process.env[envName] || path.join(ROOT, rel), "utf8");
}
const renderJs = read("QB_RENDER_JS", "frontend/qb-render.js");
const layoutJs = read("QB_EXAM_LAYOUT_JS", "frontend/exam-layout.js");

test("表格首行整行都是表头格时，打上表头行标记", () => {
  assert.match(
    renderJs,
    /tr\.classList\.add\("qb-table-head-row"\)/,
    "首行的表头标记没打上",
  );
  // 必须整行都是表头格才算表头行：半表头半数据的行重复出来是错的。
  assert.match(
    renderJs,
    /row\.every\(\(cell\) => cell\.header \|\| table\.header\)/,
    "表头行的判定不能只看其中一格",
  );
});

test("续页时把表头行克隆一份放到片段表格最上面", () => {
  assert.match(
    layoutJs,
    /table\.querySelector\("tr\.qb-table-head-row"\)/,
    "续页时找不到表头行",
  );
  assert.match(
    layoutJs,
    /insertBefore\(source\.cloneNode\(true\), table\.firstElementChild\)/,
    "表头行必须插到片段表格的第一行（最上面）",
  );
});

test("片段自带表头行时不要重复再加一行", () => {
  assert.match(
    layoutJs,
    /if \(table\.querySelector\("tr\.qb-table-head-row"\)\) return;/,
    "切点刚好在表头之后时，会印出两行表头",
  );
});

test("不许再靠 thead 找表头：渲染里根本没有 thead", () => {
  // 只查真正的 DOM 调用，别把注释里提到的 thead 也算进来。
  assert.doesNotMatch(
    layoutJs,
    /querySelector(?:All)?\(\s*["'`]thead/,
    "又在找 <thead> 了 —— 渲染出来的表格没有 thead，这段是死代码",
  );
});
