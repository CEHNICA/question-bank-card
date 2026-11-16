/* 第十四轮守护测试：时间显示的正确性与健壮性。
 *
 * 覆盖两类缺陷：
 *   1. 相对时间算错天（跨夜说成"今天"、跨年丢年份、未来时间也说"今天"）
 *   2. 无效时间戳直接显示 "Invalid Date"；toLocale* 少了 locale 跟系统语言走
 *
 * 第 1 类用真函数跑真时间戳（不靠源码长相），第 2 类用静态扫描。
 * 尺子自带自检：抽取器必须真的抽到 when()，扫描器必须能在人造坏样本上报出来，
 * 否则直接红 —— 尺子坏了整套守护测试就是摆设。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = __dirname;
const TRANSFER = path.join(FRONTEND, "library-transfer.js");

/* ---------- 尺子 1：抽取函数体 ---------- */

function extractFunction(src, signature) {
  const start = src.indexOf(signature);
  if (start < 0) return null;
  const open = src.indexOf("{", start);
  if (open < 0) return null;
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return null;
}

function loadWhen() {
  const src = fs.readFileSync(TRANSFER, "utf8");
  const code = extractFunction(src, "function when(");
  if (!code) return null;
  return eval("(" + code + ")");
}

/* ---------- 尺子 2：静态扫无效时间戳 / 缺 locale ---------- */

// new Date(x).toLocale*(...) —— 少了 locale 参数，跟着系统语言走
const RAW_LOCALE = /\.toLocale(String|DateString|TimeString)\(\s*\)/;
// new Date(x).toLocale*(...) 且同一行没有有效性保护
const RAW_DATE = /new Date\(([^()]*)\)\s*\.\s*toLocale(String|DateString|TimeString)\(/;

function scanSource(src, file) {
  const lines = src.split("\n");
  const problems = [];
  lines.forEach((line, index) => {
    const code = line.split("//")[0];
    if (!code.trim()) return;
    if (RAW_LOCALE.test(code)) {
      problems.push({ file, line: index + 1, why: "toLocale* 少了 locale 参数", code: code.trim() });
    }
    RAW_DATE.lastIndex = 0;
    const match = RAW_DATE.exec(code);
    if (match && !/\?|\|\||getTime\(\)|isNaN|isFinite|时间未知/.test(code)) {
      problems.push({ file, line: index + 1, why: "时间戳无效时可能显示 Invalid Date", code: code.trim() });
    }
  });
  return problems;
}

function scanFile(file) {
  return scanSource(fs.readFileSync(path.join(FRONTEND, file), "utf8"), file);
}

const TIME_FILES = [
  "library.js",
  "library-transfer.js",
  "library-tag-manager.js",
  "library-answer-editor.js",
  "library-question-editor.js",
  "original-paper-layout.js",
  path.join("eagle", "index.html"),
];

const EAGLE = path.join(FRONTEND, "eagle", "index.html");

/* ---------- 尺子自检：尺子坏了要立刻炸 ---------- */

test("尺子自检：能抽到 when() 且抽到的确实是对的时间文案", () => {
  const when = loadWhen();
  assert.ok(when, "抽不到 library-transfer.js 里的 when()，尺子坏了");
  assert.match(when.toString(), /function when/, "抽出来的东西不是 when()");
  const out = when(new Date(2026, 10, 16, 3, 0).toISOString(), new Date(2026, 10, 16, 10, 0));
  assert.ok(typeof out === "string" && out.length > 0, "when() 没有输出文案");
});

test("尺子自检：静态扫描器能在人造坏样本上报出来", () => {
  // 前提：无 locale 的 toLocaleString 确实是坏样本
  assert.match(
    'x.toLocaleString()',
    RAW_LOCALE,
    "扫描器连人造坏样本都认不出来，尺子坏了"
  );
  const problems = scanSource('const a = new Date(rec.created_at).toLocaleString();', "fake.js");
  assert.ok(problems.length >= 1, "扫描器没报出坏样本，尺子坏了：" + JSON.stringify(problems));
  assert.ok(
    problems.some((p) => /locale/.test(p.why)) && problems.some((p) => /Invalid/.test(p.why)),
    "扫描器报的种类不对：" + JSON.stringify(problems)
  );
  // 有保护的写法不该被报
  const ok = scanSource('const a = rec.created_at ? new Date(rec.created_at).toLocaleString("zh-CN") : "时间未知";', "fake.js");
  assert.equal(ok.length, 0, "扫描器把有保护的写法误报了：" + JSON.stringify(ok));
  // Date.parse 判过有限性的写法同样算有保护，不能误报
  const parsed = scanSource(
    'if (x && Number.isFinite(Date.parse(x))) show(new Date(x).toLocaleTimeString("zh-CN"));',
    "fake.js"
  );
  assert.equal(parsed.length, 0, "扫描器把 Date.parse 保护的写法误报了：" + JSON.stringify(parsed));
});

/* ---------- D1：跨夜不能被说成"今天" ---------- */

test("D1：昨天深夜的记录，今早看必须说「昨天」", () => {
  const when = loadWhen();
  const now = new Date(2026, 10, 16, 8, 0);
  const yesterdayNight = new Date(2026, 10, 15, 23, 0);
  const out = when(yesterdayNight.toISOString(), now);
  assert.match(out, /昨天/, "跨夜的记录被说成：" + out);
  assert.doesNotMatch(out, /^今天/, "跨夜的记录被说成：" + out);
});

test("D1：昨天中午的记录，今早看也必须说「昨天」", () => {
  const when = loadWhen();
  const now = new Date(2026, 10, 16, 10, 0);
  const yesterday = new Date(2026, 10, 15, 10, 0);
  const out = when(yesterday.toISOString(), now);
  assert.match(out, /昨天/, "同一天的记录被说成：" + out);
});

test("D1：今天早上的记录必须说「今天」", () => {
  const when = loadWhen();
  const now = new Date(2026, 10, 16, 10, 0);
  const morning = new Date(2026, 10, 16, 3, 0);
  const out = when(morning.toISOString(), now);
  assert.match(out, /今天/, "今天的记录被说成：" + out);
});

/* ---------- D2：跨年必须带年份 ---------- */

test("D2：跨年的记录必须带上年份", () => {
  const when = loadWhen();
  const now = new Date(2026, 0, 5, 10, 0);
  const lastYear = new Date(2025, 11, 20, 3, 0);
  const out = when(lastYear.toISOString(), now);
  assert.match(out, /2025/, "跨年记录没带年份：" + out);
});

test("D2：同年的老记录不用带年份", () => {
  const when = loadWhen();
  const now = new Date(2026, 10, 16, 10, 0);
  const earlier = new Date(2026, 8, 1, 3, 0);
  const out = when(earlier.toISOString(), now);
  assert.doesNotMatch(out, /2026/, "同年记录多带了年份：" + out);
});

/* ---------- D3：未来时间不能说成"今天" ---------- */

test("D3：晚于现在的时间戳不能被说成「今天」", () => {
  const when = loadWhen();
  const now = new Date(2026, 10, 16, 10, 0);
  const future = new Date(2026, 10, 17, 3, 0);
  const out = when(future.toISOString(), now);
  assert.doesNotMatch(out, /今天/, "未来时间被说成：" + out);
  assert.doesNotMatch(out, /-\d+\s*天前/, "未来时间算出了负天数：" + out);
  assert.doesNotMatch(out, /天前/, "未来时间被说成：" + out);
  assert.ok(out.length > 0, "未来时间没有任何提示");
});

/* ---------- 无效时间戳 ---------- */

test("无效时间戳不能显示成 Invalid Date", () => {
  const when = loadWhen();
  const now = new Date(2026, 10, 16, 10, 0);
  const out = when("not-a-date", now);
  assert.doesNotMatch(out, /Invalid/i, "无效时间戳显示成：" + out);
});

/* ---------- D4：Eagle 文件夹创建时间，无效时不能说成 Invalid Date ---------- */

test("D4：文件夹创建时间无效时不能显示 Invalid Date", () => {
  const src = fs.readFileSync(EAGLE, "utf8");
  const code = extractFunction(src, "function formatFolderCreatedLabel(");
  assert.ok(code, "抽不到 formatFolderCreatedLabel，尺子坏了");
  const fn = eval("(" + code + ")");
  assert.doesNotMatch(String(fn("坏值")), /Invalid/i, "无效时间显示成：" + fn("坏值"));
  assert.doesNotMatch(String(fn(NaN)), /Invalid/i, "NaN 时间显示成：" + fn(NaN));
  assert.match(String(fn(NaN)), /未知/, "无效时间没说未知：" + fn(NaN));
  assert.match(String(fn(1700000000000)), /2023/, "有效时间格式不对：" + fn(1700000000000));
});

test("各页面显示时间的地方都不能留下裸的 toLocaleString()", () => {
  const problems = TIME_FILES.flatMap((file) => scanFile(file));
  const detail = problems.map((p) => `${p.file}:${p.line} ${p.why}`).join("\n");
  assert.equal(problems.length, 0, "还有这些地方会出问题：\n" + detail);
});
