"use strict";

// 原图题（body_mode='source_image'）在 Eagle 题库工作台里曾经是一片空白。
//
// 原因不是数据丢了，也不是裁片图打不开 —— 后端 content.question_images 一直在，
// /api/library/<id>/question-images/*.png 也一直 200。问题是前端的
// convertLibraryListItem 只认 content.figures：原图题没有文字题干（stem 为空）、
// 也没有 figures，于是拼出来的 q.content 是空串，卡片正文就什么都没有。
//
// library.html 那条路线有处理 source_image，Eagle 这条没有 —— 同一个数据，
// 两个页面一个能看一个不能看，最容易误判成「数据坏了」。
//
// 这条测试盯住三件事：
//   1. 转换函数必须读 content.question_images，裁片 URL 必须原样带出来；
//   2. 正文里必须真的产出 <img>，不能只是把数据读进来不用；
//   3. 紧凑速览行不能拿「题目内容」糊弄一道原图题。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const js = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

// ---- 1. 把转换函数和它的依赖切出来 ----
const convertStart = js.indexOf("function convertLibraryListItem(");
const convertEnd = js.indexOf("\n    function ", convertStart + 10);
assert.ok(convertStart > 0 && convertEnd > convertStart, "convertLibraryListItem 必须能被切出来");
const convertBody = js.slice(convertStart, convertEnd);

const escapeStart = js.indexOf("function escapeHtml(");
const escapeEnd = js.indexOf("\n    function ", escapeStart + 10);
const escapeBody = js.slice(escapeStart, escapeEnd);

const cleanStart = js.indexOf("function getCleanInlineStem(");
const cleanEnd = js.indexOf("\n    function ", cleanStart + 10);
assert.ok(cleanStart > 0 && cleanEnd > cleanStart, "getCleanInlineStem 必须存在");
const cleanBody = js.slice(cleanStart, cleanEnd);

const inlineStart = js.indexOf("function inlineStemPreview(");
const inlineEnd = js.indexOf("\n    function ", inlineStart + 10);
assert.ok(inlineStart > 0 && inlineEnd > inlineStart, "inlineStemPreview 必须存在（原图题速览行要有专门的文案）");
const inlineBody = js.slice(inlineStart, inlineEnd);

const scope = {
  API_BASE: "",
  LIBRARY_TYPE_NAMES: { single_choice: "单选题", free_response: "解答题" },
  escapeHtml: (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  getDifficultyOverrides: () => ({}),
  renderAdaptiveOptionsHtml: () => "",
  buildPendingTagSuggestions: () => [],
};
vm.runInNewContext(escapeBody + "\n" + cleanBody + "\n" + inlineBody + "\n" + convertBody, scope);
assert.equal(typeof scope.convertLibraryListItem, "function");
assert.equal(typeof scope.inlineStemPreview, "function");

// ---- 2. 读到了裁片 ----
const piece = {
  page_idx: 0,
  bbox: [0, 0, 1000, 200],
  width: 2000,
  height: 280,
  file: "question-1.png",
  url: "/api/library/1e3b70de-17d7-4566-bfe4-c6f34ee84335/question-images/question-1.png",
};

const item = {
  id: "1e3b70de-17d7-4566-bfe4-c6f34ee84335",
  document_id: "1460ce13-ace7-472c-be53-26f81faa7a02",
  source_filename: "原图题体检卷.png",
  number: 1,
  question_type: "free_response",
  content: {
    stem: "",
    options: {},
    answer: "",
    analysis: "",
    figures: [],
    body_mode: "source_image",
    question_images: [piece],
  },
};

const q = scope.convertLibraryListItem(item, 0);

assert.equal(q.isSourceImage, true, "原图题必须被标出来");
assert.equal(q.questionImages.length, 1, "裁片必须被带出来");
assert.deepEqual(
  q.questionImages.map((p) => p.url),
  [piece.url],
  "裁片 URL 必须原样带出来，不能被改写"
);

// ---- 3. 正文里真的产出了图（关键） ----
assert.ok(/<img\b/.test(q.content), `正文里必须有 <img>，实际是：${String(q.content).slice(0, 200)}`);
assert.ok(
  q.content.includes(piece.url),
  `正文里的图必须指向真实裁片地址，实际是：${String(q.content).slice(0, 300)}`
);
assert.ok(
  /alt="原图题第 1 段[^"]*原卷第 1 页/.test(q.content),
  "图要有可读的替代文字（段序 + 原卷页码）"
);

// 裁片 URL 里的 & 之类不能把属性截断
const ampItem = JSON.parse(JSON.stringify(item));
ampItem.content.question_images = [{ ...piece, url: "/api/library/x/question-images/q-1.png?a=1&b=2" }];
const qAmp = scope.convertLibraryListItem(ampItem, 0);
assert.ok(qAmp.content.includes("&amp;"), "URL 里的 & 必须转义，否则属性会被截断");

// ---- 4. 多段裁片按原顺序全贴出来 ----
const multiItem = JSON.parse(JSON.stringify(item));
multiItem.content.question_images = [
  { ...piece, file: "question-1.png", url: "/api/library/x/question-images/question-1.png" },
  { ...piece, file: "question-2.png", url: "/api/library/x/question-images/question-2.png" },
  { ...piece, file: "question-3.png", url: "/api/library/x/question-images/question-3.png" },
];
const qMulti = scope.convertLibraryListItem(multiItem, 0);
const srcs = [...qMulti.content.matchAll(/src="([^"]+)"/g)].map((m) => m[1]);
assert.deepEqual(
  srcs,
  multiItem.content.question_images.map((p) => p.url),
  "多段裁片要按保存顺序全部贴出来"
);

// ---- 5. 普通题不受影响 ----
const plainItem = {
  id: "plain-1",
  number: 3,
  question_type: "single_choice",
  content: { stem: "已知函数 $f(x)$", options: { A: "1" }, answer: "", analysis: "", figures: [] },
};
const qPlain = scope.convertLibraryListItem(plainItem, 0);
assert.equal(qPlain.isSourceImage, false, "普通题不能被误判成原图题");
assert.ok(!/<img\b/.test(qPlain.content), "普通题不该凭空多出图");
assert.equal(scope.inlineStemPreview(qPlain), "已知函数 $f(x)$", "普通题的速览行照旧");

// ---- 6. 速览行不能拿「题目内容」糊弄原图题 ----
const preview = scope.inlineStemPreview(q);
assert.ok(
  preview.includes("原图题") && preview.includes("1 段"),
  `原图题的速览行要说明是原卷裁片，实际是：${preview}`
);
assert.equal(scope.inlineStemPreview(qMulti), "原图题 · 原卷裁片 3 段");
assert.equal(scope.inlineStemPreview({ rawStem: "" }), "题目内容", "真没内容的题才退回「题目内容」");

// ---- 7. Eagle 页不许再出现「读到了却不贴」的写法 ----
assert.match(
  convertBody,
  /question_images/,
  "convertLibraryListItem 必须读 content.question_images"
);
assert.match(
  convertBody,
  /rawQuestionImages\.length\s*>\s*0/,
  "必须真的按裁片数量分支，把图贴进正文"
);

// 卡片正文只有一处出口：q.content。裁片要是没进 q.content，
// 渲染再对也是空的 —— 这条断言就是盯这个。
const cardRender = js.slice(js.indexOf("function renderSingleQuestionCardHtml("), js.indexOf("function renderCompactQuestionRowHtml("));
assert.match(cardRender, /\$\{sanitizeMathHtml\(q\.content\)\}/, "卡片正文必须来自 q.content");
