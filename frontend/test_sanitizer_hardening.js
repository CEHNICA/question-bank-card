/* 第十五轮守护测试：内容净化与转义。
 *
 * 覆盖三类缺陷：
 *   D1  Eagle 的 sanitizeMathHtml 是假 sanitizer —— 只挡了双引号事件属性和
 *       成对 <script>，单引号 / 无引号事件属性、<svg onload>、无闭合 <script>、
 *       <iframe>、javascript: 伪协议全部漏网（浏览器里真的执行了脚本）。
 *   D2  经典页「👁 查看解析」把接口返回的**对象**当字符串用 —— 明明有解析，
 *       点进去却显示「未能读取到原卷解析」。
 *   D3  同一处把解析正文直接塞 innerHTML，注入面一直敞着。
 *
 * D1 靠真函数跑真用例（不靠源码长相），D2/D3 靠抽出纯函数跑真数据。
 * D1 的用例按「挡什么」分成三组，每组一条断言 —— 这样退一处修复时，
 * 看门狗能精确红在自己那条上。
 *
 * 尺子自带自检：必须真的抽到净化器 / 取值函数，而且旧版弱实现必须在这批用例上
 * 漏出足够多的网 —— 否则断言恒真，整套守护测试就是摆设。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = __dirname;
const EAGLE = path.join(FRONTEND, "eagle", "index.html");
const FILTER_BAR = path.join(FRONTEND, "eagle-filter-bar.js");

/* ---------- 尺子：抽取 ---------- */

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

// 净化器是一整块（常量 + 辅助函数 + 主函数），按标记切出来整体 eval，
// 免得抽到一半缺依赖。
function extractSanitizerBlock(src) {
  const start = src.indexOf("SANITIZER-BLOCK-START");
  const end = src.indexOf("SANITIZER-BLOCK-END");
  if (start < 0 || end < 0 || end < start) return null;
  const from = src.indexOf("*/", start);
  if (from < 0) return null;
  const cut = src.lastIndexOf("/*", end - 1);
  if (cut < 0 || cut <= from) return null;
  return src.slice(from + 2, cut);
}

function loadSanitizer() {
  const src = fs.readFileSync(EAGLE, "utf8");
  const block = extractSanitizerBlock(src);
  if (!block) return null;
  try {
    const fn = new Function(block + "\n; return typeof sanitizeMathHtml === 'function' ? sanitizeMathHtml : null;")();
    return typeof fn === "function" ? fn : null;
  } catch (err) {
    return null;
  }
}

function loadExtractSolutionText() {
  const src = fs.readFileSync(FILTER_BAR, "utf8");
  const code = extractFunction(src, "function extractSolutionText(");
  if (!code) return null;
  try {
    return new Function(code + "\n; return extractSolutionText;")();
  } catch (err) {
    return null;
  }
}

/* ---------- 用例 ---------- */

// D1-1：事件属性（双引号 / 单引号 / 无引号 / 大写 / 斜杠分隔 / svg / body）
const EVENT_CASES = [
  ["双引号事件属性", `<img src=x onerror="window.__pwned=1">`, /onerror/i],
  ["单引号事件属性", `<img src=x onerror='window.__pwned=1'>`, /onerror/i],
  ["无引号事件属性", `<img src=x onerror=window.__pwned=1>`, /onerror/i],
  ["大写事件属性", `<img src=x ONERROR="x">`, /onerror/i],
  ["斜杠分隔事件属性", `<img/onerror=alert(1) src=x>`, /onerror/i],
  ["svg onload", `<svg onload=window.__pwned=1></svg>`, /onload/i],
  ["body onload", `<body onload=alert(1)>`, /onload/i],
];

// D1-2：危险元素（script / iframe / object / embed / base / style）
const ELEMENT_CASES = [
  ["成对 script", `<script>window.__pwned=1</script>`, /<script/i],
  ["无闭合 script", `<script src=x>`, /<script/i],
  ["嵌套 script", `<div><script>alert(1)</script></div>`, /<script/i],
  ["iframe", `<iframe src="data:text/html,<script>x</script>"></iframe>`, /<iframe/i],
  ["object", `<object data="x.swf"></object>`, /<object/i],
  ["embed", `<embed src="x.swf">`, /<embed/i],
  ["base", `<base href="javascript:alert(1)">`, /<base/i],
  ["style 块", `<style>.a{}</style>ok`, /<style/i],
];

// D1-3：伪协议（javascript: / vbscript: / data:text/html）
const URL_CASES = [
  ["javascript 伪协议", `<a href="javascript:window.__pwned=1">点我</a>`, /javascript:/i],
  ["大写 javascript", `<a href="JAVASCRIPT:alert(1)">x</a>`, /javascript:/i],
  ["前导空格的 javascript", `<a href=" javascript:alert(1)">x</a>`, /javascript:/i],
  ["实体混淆的 javascript", `<a href="jav&#x61;script:alert(1)">x</a>`, /javascript:/i],
  ["data:text/html 伪协议", `<a href="data:text/html,<script>alert(1)</script>">x</a>`, /data:text\/html/i],
  ["form action 伪协议", `<form action="javascript:alert(1)"><input></form>`, /javascript:/i],
  ["formaction 伪协议", `<button formaction="javascript:alert(1)">x</button>`, /javascript:/i],
];

const ALL_BLOCK_CASES = [...EVENT_CASES, ...ELEMENT_CASES, ...URL_CASES];

// 必须原样保留：公式、图片、MathML、以及正文里的尖括号
const KEEP_CASES = [
  ["尖括号吃正文", "解：a<b>c 则 b>0"],
  ["小于号", "当 x < y 时"],
  ["未闭合尖括号", "a<b"],
  ["公式原样", "$$\\frac{1}{2}$$"],
  ["katex 标签", `<span class="katex">x</span>`],
  ["MathML", `<math><mi>x</mi></math>`],
  ["内嵌图片", `<img src="data:image/png;base64,AAA">`],
  ["属性里带大于号", `<p title="a > b">x</p>`],
  ["正文里的 on= 不是属性", "若 on=1 则"],
];

/* ---------- 尺子自检 ---------- */

// 旧版弱实现：只挡双引号事件属性和成对 <script>
function legacySanitize(html) {
  if (!html) return "";
  return String(html)
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/on\w+="[^"]*"/gi, "");
}

test("尺子自检：旧版弱实现在这批用例上必须漏网（否则断言恒真）", () => {
  const leaked = ALL_BLOCK_CASES.filter(([, html, re]) => re.test(legacySanitize(html)));
  assert.ok(leaked.length >= 5, `旧实现只漏了 ${leaked.length} 条，用例不够狠，断言会恒真`);
});

test("尺子自检：必须抽到净化器区块", () => {
  const src = fs.readFileSync(EAGLE, "utf8");
  const block = extractSanitizerBlock(src);
  assert.ok(block, "eagle/index.html 里找不到 SANITIZER-BLOCK 标记");
  assert.ok(/sanitizeMathHtml/.test(block), "净化器区块里没有 sanitizeMathHtml");
  const fn = loadSanitizer();
  assert.ok(typeof fn === "function", "净化器区块抽不出可调用的 sanitizeMathHtml");
});

test("尺子自检：必须抽到 extractSolutionText", () => {
  const fn = loadExtractSolutionText();
  assert.ok(typeof fn === "function", "eagle-filter-bar.js 里抽不到 extractSolutionText");
});

/* ---------- D1：净化器行为 ---------- */

function assertBlocked(cases) {
  const fn = loadSanitizer();
  assert.ok(typeof fn === "function", "抽不到 sanitizeMathHtml");
  const leaks = [];
  for (const [name, html, re] of cases) {
    let out;
    try {
      out = fn(html);
    } catch (err) {
      leaks.push(`${name}：净化器自己抛异常 ${err && err.message}`);
      continue;
    }
    if (typeof out !== "string") {
      leaks.push(`${name}：输出不是字符串`);
      continue;
    }
    if (re.test(out)) leaks.push(`${name}：输出仍有可执行痕迹 ${JSON.stringify(out)}`);
  }
  assert.deepStrictEqual(leaks, [], leaks.join("\n"));
}

test("D1-1：事件属性必须被挡下", () => assertBlocked(EVENT_CASES));
test("D1-2：危险元素必须被挡下", () => assertBlocked(ELEMENT_CASES));
test("D1-3：伪协议必须被挡下", () => assertBlocked(URL_CASES));

test("D1-4：公式、图片、正文尖括号必须原样保留", () => {
  const fn = loadSanitizer();
  assert.ok(typeof fn === "function", "抽不到 sanitizeMathHtml");
  const broken = [];
  for (const [name, html] of KEEP_CASES) {
    const out = fn(html);
    if (out !== html) broken.push(`${name}：正文被改坏了 ${JSON.stringify(out)}（原 ${JSON.stringify(html)}）`);
  }
  assert.deepStrictEqual(broken, [], broken.join("\n"));
});

test("D1-5：净化器必须接住空值与非字符串", () => {
  const fn = loadSanitizer();
  assert.strictEqual(fn(""), "");
  assert.strictEqual(fn(null), "");
  assert.strictEqual(fn(undefined), "");
  assert.strictEqual(fn(0), "");
  assert.strictEqual(typeof fn(123), "string");
});

/* ---------- D2 / D3：经典页「查看解析」 ---------- */

test("D2：接口返回的对象必须取出字符串，不能把对象当正文", () => {
  const fn = loadExtractSolutionText();
  assert.ok(typeof fn === "function", "抽不到 extractSolutionText");
  const cases = [
    [{ solution: { answer: "A", analysis: "解：a<b>c" } }, "解：a<b>c"],
    [{ solution: { answer: "A", analysis: "" } }, "A"],
    [{ solution: null }, ""],
    [{ solution: undefined }, ""],
    [{ analysis: "原卷解析" }, "原卷解析"],
    [{ content: { analysis: "内容解析" } }, "内容解析"],
    [{}, ""],
    [null, ""],
    [undefined, ""],
  ];
  const broken = [];
  for (const [payload, expected] of cases) {
    const out = fn(payload);
    if (typeof out !== "string") {
      broken.push(`${JSON.stringify(payload)}：返回的不是字符串（${typeof out}）`);
      continue;
    }
    if (out !== expected) broken.push(`${JSON.stringify(payload)}：期望 ${JSON.stringify(expected)}，实际 ${JSON.stringify(out)}`);
  }
  assert.deepStrictEqual(broken, [], broken.join("\n"));
});

test("D3：解析正文不得当 HTML 塞进 DOM", () => {
  const src = fs.readFileSync(FILTER_BAR, "utf8");
  assert.ok(!/innerHTML\s*=\s*content\b/.test(src), "解析正文仍然直接赋给 innerHTML");
  assert.ok(!/solEl\.innerHTML\s*=/.test(src), "solEl 仍然用 innerHTML 写正文");
  assert.ok(/solEl\.textContent\s*=/.test(src), "solEl 应该用 textContent 写正文");
  assert.ok(/extractSolutionText\(/.test(src), "解析取值没有走 extractSolutionText");
});

test("D1-6：题干渲染点必须仍然过净化器", () => {
  const src = fs.readFileSync(EAGLE, "utf8");
  const uses = (src.match(/sanitizeMathHtml\(/g) || []).length;
  // 1 处 sanitizer 定义 + 3 处题卡正文/QuickLook 渲染调用（原独立改题弹窗已合入统一工作台并改走 QB.renderQuestion DOM 渲染）
  assert.ok(uses >= 4, `sanitizeMathHtml 只被用了 ${uses} 次，题干渲染点可能被绕过`);
  assert.ok(!/innerHTML\s*=\s*q\.content\b/.test(src), "题干被直接赋给 innerHTML，绕过了净化器");
  assert.ok(!/innerHTML\s*=\s*q\.rawStem\b/.test(src), "原始题干文本不得直接赋给 innerHTML");
});
