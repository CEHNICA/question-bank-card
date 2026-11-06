"use strict";
// 1.26.38：重新切题预演弹窗里那颗「确认并应用重新切题」，灰着时必须开口说话。
//
// 这次全软件扫了一遍灰控件（主界面、试卷题目页、资料库页、每个弹窗、每个折叠块），
// 就剩这一处是真问题：别的灰控件要么带说明，要么整块藏着看不见，只有这颗
// 摆在明面上、灰着、一句话不说。
//
// 两条要求：
//   1) 灰控件要说清「为什么灰」和「下一步去哪」—— 两种说法都要在，不能只说做什么；
//   2) 不同状态说的话必须不一样（不能所有情况都同一句），同一状态必须稳定（不许飘）。
//   3) 长版（悬停说明）和短版（按钮旁边那行常驻小字）都要在。
//      短版不能是长版的半句 —— 截半句会变成半句话，更看不懂。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const W = require("./resegment-preview-wording.js");

const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const wordingJs = fs.readFileSync(path.join(__dirname, "resegment-preview-wording.js"), "utf8");

const READONLY_REPORT = { read_only: true, summary: {}, items: {} };

// 六种状态：预演在算 / 预演没跑成 / 还没有预演 / 服务端不认 / 还没勾确认框 / 可以应用了
const CASES = [
  { key: "loading", state: { loading: true }, must: ["正在", "预演"] },
  { key: "blocked", state: { blocked: true }, must: ["没跑成功", "原因"] },
  { key: "noReport", state: {}, must: ["重新切题"] },
  { key: "notReadOnly", state: { report: { read_only: false }, readOnly: false }, must: ["只读", "禁止"] },
  { key: "notAcknowledged", state: { report: READONLY_REPORT, readOnly: true }, must: ["确认框"] },
  { key: "ready", state: { report: READONLY_REPORT, readOnly: true, acknowledged: true }, must: ["应用"] },
];

// ---- 长版：悬停说明 --------------------------------------------------
for (const item of CASES) {
  const hint = W.applyHint(item.state);
  assert.equal(typeof hint, "string", item.key + "：必须有话说");
  assert.ok(hint.trim().length > 0, item.key + "：不能是空话");
  for (const word of item.must) {
    assert.ok(hint.includes(word), item.key + "：话术里要提到「" + word + "」，实际是「" + hint + "」");
  }
  // 同一个状态，调两次必须一样 —— 说话不许飘
  assert.equal(W.applyHint(item.state), hint, item.key + "：同一个状态说的话必须稳定");
}

// 六种状态的话必须互不相同，不能所有情况都同一句
const hints = CASES.map((item) => W.applyHint(item.state));
assert.equal(new Set(hints).size, hints.length, "六种状态必须各说各的，不能都同一句");

// ---- 短版：按钮旁边那行常驻小字 --------------------------------------
const briefs = CASES.map((item) => W.applyHintBrief(item.state));
assert.equal(new Set(briefs.filter(Boolean)).size, briefs.filter(Boolean).length,
  "短版也必须各说各的，不能都同一句");
for (const item of CASES) {
  const brief = W.applyHintBrief(item.state);
  if (item.key === "ready") {
    assert.equal(brief, "", "可以应用时不该再提示差哪一步");
    continue;
  }
  assert.ok(brief.trim().length > 0, item.key + "：短版不能是空的");
  // 短版是另写的一句，不是长版截半句
  const long = W.applyHint(item.state);
  assert.notEqual(brief, long.slice(0, brief.length), item.key + "：短版不能是长版截半句");
  assert.ok(brief.length < long.length, item.key + "：短版要比长版短");
}

// 短版表里六种情况都要有，别漏
for (const key of ["applying", "loading", "blocked", "noReport", "notReadOnly", "notAcknowledged"]) {
  assert.ok(Object.prototype.hasOwnProperty.call(W.BRIEF, key), "短版表里少了 " + key);
}
assert.equal(W.BRIEF.ready, "", "可以应用时短版是空的");

// 「正在应用」这状态也得有话说（应用中途按钮也灰着）
assert.ok(W.applyHint({ applying: true }).includes("正在"), "正在应用时要说明在等");
assert.ok(W.applyHintBrief({ applying: true }).length > 0, "正在应用时短版不能是空的");

// ---- 接线：按钮上必须真的挂上话术 ----------------------------------
// 上一轮的教训：光验话术函数不算数。函数写得再对，按钮忘了挂上去，
// 老师看到的还是那个不吭声的控件。所以这里要盯住 app.js 里的接线。
assert.match(appJs, /\$\("resegmentApply"\)\.title = QBResegmentWording\.applyHint\(status\)/,
  "按钮的悬停说明必须接到共用话术上");
assert.match(appJs, /visible\.textContent = QBResegmentWording\.applyHintBrief\(status\)/,
  "按钮旁边那行常驻小字必须接到共用短版话术上");
assert.match(appJs, /QBResegmentWording = \(typeof window/, "app.js 必须从独立模块取话术");
assert.doesNotMatch(appJs, /function applyHint\(/, "app.js 里不该再留一份重复的话术实现");
assert.doesNotMatch(appJs, /function applyHintBrief\(/, "app.js 里不该再留一份重复的短版实现");

// 所有让按钮变灰的地方，都得顺手刷一次话术
const syncCalls = appJs.split("syncResegmentApply();").length - 1;
assert.ok(syncCalls >= 6, "六种状态都要刷话术，实际刷了 " + syncCalls + " 次");

// ---- 页面结构：得有一行看得见的提示 --------------------------------
assert.match(html, /id="resegmentApplyHint"/, "弹窗里要有那行常驻可见的提示");
assert.match(html, /id="resegmentApplyHint"[^>]*role="status"/, "那行提示要能被读屏软件读到");
assert.match(html, /id="resegmentApply"[^>]*disabled/, "那颗按钮初始就是灰的");
// 只看这颗按钮自己的标签：不能跨过整个文件去匹配别的按钮的 title。
// （上一轮 M14 就是这么漏网的 —— 断言写成「文件里某处有 title」等于没验。）
const applyTag = html.match(/<button[^>]*id="resegmentApply"[^>]*>/)?.[0] || "";
assert.ok(applyTag, "页面上要能找到那颗按钮");
assert.match(applyTag, /title="[^"]+"/,
  "按钮上要带一句默认说明，页面刚打开、脚本还没跑的时候也不至于一声不吭");
assert.match(applyTag, /\bdisabled\b/,
  "那颗按钮初始就是灰的，灰着就该有话说");
assert.match(css, /\.resegment-apply-hint\s*\{/, "那行提示要有样式");
assert.match(css, /\.resegment-apply-hint:empty\s*\{[^}]*display:\s*none/,
  "没话说的时候这行字不能占地方");
assert.match(html, /<script src="\/resegment-preview-wording\.js" defer><\/script>/,
  "页面要先加载话术模块再加载 app.js");
assert.match(html, /<script src="\/resegment-preview-wording\.js" defer><\/script>\s*<script src="\/app\.js"/,
  "话术模块必须在 app.js 之前加载");
assert.match(wordingJs, /root\.QBResegmentWording = value/, "话术模块要挂到 window 上");

console.log("resegment apply grey-button wording: OK");
