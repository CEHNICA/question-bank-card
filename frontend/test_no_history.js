"use strict";

// 1.12.6：删掉「版本历史」。这是一个只读弹窗，没有任何别的功能依赖它
// （撤回走独立端点，题卡的「有改动未入库」走 publication.up_to_date），
// 所以整块删除而不是隐藏。这里钉住它不再回来。
//
// 反向断言比正向断言有用：功能消失时没有任何代码会失败，只有明确写着
// 「不许再有」的那几条会失败。

const assert = require("node:assert/strict");
const fs = require("node:fs");

const read = (name) => fs.readFileSync(require.resolve(`./${name}`), "utf8");
const libraryJs = read("library.js");
const libraryHtml = read("library.html");
const libraryCss = read("library.css");

for (const [name, text] of [["library.js", libraryJs], ["library.html", libraryHtml]]) {
  for (const gone of ["版本历史", "historyDialog", "i-history", "openHistory", "loadHistoryVersion",
    "historyState", "historyCompare", "historyVersions", "history-panel", "history-version"]) {
    assert.ok(!text.includes(gone), `${name} 里不该再有 ${gone}`);
  }
}
assert.doesNotMatch(libraryCss, /\.history-(dialog|layout|versions|version|content|controls|preview|panel|diff|change|origin|status)\b/,
  "版本历史的样式应整块删除");

// 核心功能不受影响：撤回是另一条路，题卡和详情弹窗都还要有它。
assert.match(libraryJs, /button button-quiet button-small library-withdraw", "撤回"/,
  "题卡「更多」里的撤回必须保留");
assert.doesNotMatch(libraryJs, /历史版本都保留/, "撤回的说明里不该再提历史版本");
assert.match(libraryJs, /actions\.append\(source, editQuestion, editAnswer/,
  "题目详情里仍有来源、修改题目、修改答案与解析");

// 「出处」弹窗仍会列出其他资料中的相同题面，只是每条不再带「查看历史」。
assert.match(libraryJs, /题面相同的其他资料/);
assert.match(libraryJs, /题面相近的其他资料（需核对）/);
assert.match(libraryJs, /"查看原卷"/);
assert.doesNotMatch(libraryJs, /查看这份资料的历史/);

// 出处用的那条接口还在（改名为 fetchPublication），只是不再用于历史窗口。
assert.match(libraryJs, /async function fetchPublication\(/);
assert.match(libraryJs, /fetchPublication\(item\.id\)\.then/);

console.log("No version history: the read-only history dialog, its styles and its self-check script are gone; 撤回 and 查看出处 keep working: OK");
