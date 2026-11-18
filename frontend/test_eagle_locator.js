"use strict";

// Eagle 工作台的卡片墙（题库工作台）以前直接拿题号拼一句「第 N 题」。
//
// 同一本教材里题号从小节重新开始，「第 1 题」在书上有一百多处 —— 810 道题里
// 128 道都叫「第 1 题」，老师根本认不出是哪道题。全库只有一套说法
// （frontend/question-locator.js）：大标题说「这是第几题」（identityOf），
// 小字说「在书上第几页、哪一节」（positionOf）。页码是唯一的硬锚点。
//
// 回收站那一头：所有请求都得带 X-QB-Request 头，否则后端守卫一律 403 ——
// 「移入回收站」从来没成功过，面板里的东西自然永远是空。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const L = require("./question-locator.js");
const js = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

// ---- 1. Eagle 页必须走统一模块 ----
assert.match(js, /QBQuestionLocator/, "Eagle 页必须用统一定位模块 QBQuestionLocator");

// ---- 2. 两个 helper 的行为 ----
const identityStart = js.indexOf("function questionIdentity(");
const helperEnd = js.indexOf("function convertLibraryListItem(", identityStart);
assert.ok(identityStart > 0 && helperEnd > identityStart, "questionIdentity / questionPosition 必须存在");
const scope = { window: { QBQuestionLocator: L } };
vm.runInNewContext(js.slice(identityStart, helperEnd), scope);
assert.equal(typeof scope.questionIdentity, "function");
assert.equal(typeof scope.questionPosition, "function");

// 卡头：这是第几题。教材里题号重号，靠它加上页码才认得出。
assert.equal(scope.questionIdentity({ number: 4 }), "第 4 题");
assert.equal(scope.questionIdentity({ number: null, section: "做一做" }), "做一做");
assert.equal(scope.questionIdentity({}), "栏目题");

// 卡上小字：书上第几页、哪一节。
assert.equal(scope.questionPosition({ number: 4, page: 103 }), "第 103 页");
assert.equal(scope.questionPosition({ number: 4, page: 103, section: "习题3.4" }), "第 103 页 · 习题3.4");
assert.equal(scope.questionPosition({ number: 4 }), "");

// 模块没加载时也不能白屏，给个说得过去的默认值。
const bare = { window: {} };
vm.runInNewContext(js.slice(identityStart, helperEnd), bare);
assert.equal(bare.questionIdentity({ number: 4 }), "第 4 题");
assert.equal(bare.questionPosition({ number: 4 }), "");

// ---- 3. 列表转换必须带原卷题号与页码 ----
const convertStart = js.indexOf("function convertLibraryListItem(");
const convertEnd = js.indexOf("\n    function ", convertStart + 10);
assert.ok(convertStart > 0 && convertEnd > convertStart, "convertLibraryListItem 必须能被切出来");
const convertBody = js.slice(convertStart, convertEnd);
assert.match(convertBody, /number:\s*\(item\.number/, "必须把原卷题号原样带出来（列表序号不是题号）");
assert.match(convertBody, /page:\s*\(item\.page/, "必须带原卷页码");

// ---- 4. Eagle 页不许再自己拼「第 N 题」 ----
const direct = [...js.matchAll(/第 \$\{q\.questionNumber\} 题/g)];
assert.equal(
  direct.length,
  0,
  `Eagle 页里还有直接拼题号的地方：${direct
    .map((m) => js.slice(Math.max(0, m.index - 70), m.index + 20).replace(/\s+/g, " "))
    .join(" | ")}`
);

// ---- 5. 回收站请求必须带守卫头 ----
const TRASH_CALLS = [
  "/api/library/trash",
  "/api/library/withdraw-batch",
  "/api/library/restore-batch",
  "/api/library/purge-batch",
];
for (const url of TRASH_CALLS) {
  const index = js.indexOf(url);
  assert.ok(index > 0, `${url} 必须在 Eagle 页里被调用`);
  const around = js.slice(Math.max(0, index - 500), index + 500);
  assert.match(
    around,
    /X-QB-Request/,
    `${url} 的请求必须带 X-QB-Request 头，否则后端守卫一律 403（移入回收站从来没成功过）`
  );
}

// ---- 6. 回收站读失败不能装作是空的 ----
const trashStart = js.indexOf("async function refreshLibraryTrash(");
assert.ok(trashStart > 0, "refreshLibraryTrash 必须存在");
const trashBody = js.slice(trashStart, js.indexOf("\n    function ", trashStart + 10));
assert.ok(
  !/catch \(e\) \{\s*\}/.test(trashBody),
  "回收站读失败不许静默吞掉 —— 静默失败时面板装作是空的，用户以为没删过东西"
);

// ---- 7. 下架试题不能把「没带 ok 字段的成功」当成失败 ----
const moveQStart = js.indexOf("async function moveQuestionsToTrash(");
assert.ok(moveQStart > 0, "moveQuestionsToTrash 必须存在");
const moveQBody = js.slice(moveQStart, js.indexOf("\n    function ", moveQStart + 10));
assert.match(
  moveQBody,
  /data\.ok\s*===\s*false/,
  "只把显式 ok:false 当失败；旧后端按 ids 成功时不带 ok，!data.ok 会误报「下架试题失败」"
);
assert.doesNotMatch(
  moveQBody,
  /!resp\.ok\s*\|\|\s*!data\.ok/,
  "不许再用 !data.ok —— 那正是「移入回收站失败：下架试题失败」的根因"
);

// ---- 8. 归档/恢复/彻底删除接口与批处理分片 ----
for (const pattern of [
  /\/api\/papers\/\$\{paperId\}\/archive[\s\S]{0,220}application\/json/,
  /\/api\/papers\/\$\{paperId\}\/restore[\s\S]{0,220}application\/json/,
  /\/api\/library\/\$\{pubId\}\/restore[\s\S]{0,220}application\/json/,
  /\/api\/library\/\$\{pubId\}\/purge[\s\S]{0,220}application\/json/,
]) {
  assert.match(js, pattern, `回收站单条操作请求 (${pattern}) 必须附带 application/json 头以避免 415`);
}
const movePaperFn = js.slice(js.indexOf("async function movePaperToTrash("), js.indexOf("async function moveQuestionsToTrash("));
assert.ok(
  movePaperFn.indexOf("/archive") < movePaperFn.indexOf("/api/library/withdraw-batch"),
  "删整份资料时应先校验归档，避免归档失败却已将试题孤立下架"
);
