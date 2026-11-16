"use strict";
// 读失败被当成「没有 / 已完成」的守护测试（第十二轮：经典页 + Eagle 的错误态）。
//
// 这一轮实测出来的真缺陷（Playwright 真实浏览器 + 故障注入，逐条对照过）：
//
// D1 经典题库页（frontend/library.js）
//   后台 quiet 续拉翻页时，旧版把「失败」和「读到头了」写成一句
//     if (!more.ok || !page.items.length) break;
//   中途 500 就静默 break，上面已经拿到的那一批也被丢掉，题库凭空少一半。
//   实测旧版：故障前 120 道 -> 故障后 100 道，提示条不出现、重试按钮也没有。
//   修好后：非 2xx 抛错 -> 统一 catch 出「题库这一页没能显示出来…（重试读取）」，
//   并且保留上一次读到的 120 道（实测 120 -> 120）。
//
// D2 Eagle 单份资料的已入库试题（frontend/eagle/index.html）
//   旧版：翻页遇到非 2xx 直接 break，底下却写
//     — 《测试资料》已入库 42 题已在列表中 —
//   实测旧版：一道题都没读到，界面却宣称 42 道「已在列表中」（替失败作证）。
//   修好后：— 《测试资料》没有载入完：读取失败（HTTP 500）。列表里只有已取到的 0 道题 —
//
// D3 解析编辑器的 AI 初稿（frontend/eagle-solution-editor.js）
//   旧版：读初稿失败一律当「没有初稿」，界面写「这道题还没有 AI 初稿」。
//   实测旧版：HTTP 500 照样写「还没有 AI 初稿」，老师真去排生成任务就白跑额度。
//   修好后：AI 初稿没读到（读取解析失败（HTTP 500））：这里显示的是「读不到」，
//   不是「没有初稿」。
//
// 静态源码断言：喂修复前的文件进来必须变红（--library= / --eagle= / --sol=）。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
}

const libraryPath = arg("library") || path.join(__dirname, "library.js");
const eaglePath = arg("eagle") || path.join(__dirname, "eagle", "index.html");
const solPath = arg("sol") || path.join(__dirname, "eagle-solution-editor.js");

const library = fs.readFileSync(libraryPath, "utf8");
const eagle = fs.readFileSync(eaglePath, "utf8");
const sol = fs.readFileSync(solPath, "utf8");

/** 取某个函数体（花括号配平），按关键词定位起点。 */
function blockOf(src, keyword, startAt = 0) {
  const idx = src.indexOf(keyword, startAt);
  if (idx < 0) return null;
  const i = src.indexOf("{", idx);
  if (i < 0) return null;
  let depth = 0;
  let j = i;
  while (j < src.length) {
    if (src[j] === "{") depth += 1;
    else if (src[j] === "}") {
      depth -= 1;
      if (depth === 0) break;
    }
    j += 1;
  }
  return src.slice(i, j + 1);
}

/**
 * 取函数体：先跳过参数表（可能带解构花括号），再取函数体的花括号。
 * blockOf 直接找第一个 "{" 会在 `async function load({ append = false } = {})`
 * 这种写法上抓错地方 —— 抓到的是解构的 "{"，函数体根本没进来，断言会假红。
 */
function bodyOf(src, keyword) {
  const idx = src.indexOf(keyword);
  if (idx < 0) return null;
  const p = src.indexOf("(", idx);
  if (p < 0) return null;
  let depth = 0;
  let j = p;
  while (j < src.length) {
    if (src[j] === "(") depth += 1;
    else if (src[j] === ")") {
      depth -= 1;
      if (depth === 0) break;
    }
    j += 1;
  }
  return blockOf(src.slice(j + 1), "{", 0);
}

// ---------------------------------------------------------------- 断言

it("尺子自检：三处关键函数必须找得到（否则下面的断言会变成恒真）", () => {
  const load = bodyOf(library, "async function load(");
  assert.ok(load, "找不到 library.js 的 load()（结构变了？）");
  assert.ok(load.includes("quiet"), "load() 里没有 quiet 分支了，这条测试要跟着更新");

  const doc = bodyOf(eagle, "async function ensureDocumentQuestionsLoaded");
  assert.ok(doc, "找不到 ensureDocumentQuestionsLoaded（结构变了？）");
  assert.ok(doc.includes("/api/library?document="), "按资料补拉的请求 URL 变了");

  const draft = bodyOf(sol, "async function fetchAiDraft");
  assert.ok(draft, "找不到 fetchAiDraft（结构变了？）");
  assert.ok(draft.includes("/api/library/jobs?"), "AI 初稿的请求 URL 变了");

  assert.ok(bodyOf(sol, "function columnHtml"), "找不到 columnHtml");
});

it("D1：经典页 quiet 续拉遇到非 2xx 必须抛错，不能静默 break", () => {
  const load = bodyOf(library, "async function load(");
  assert.ok(load, "找不到 library.js 的 load()");
  assert.ok(
    /if\s*\(\s*!more\.ok\s*\)\s*throw/.test(load),
    "quiet 续拉遇到非 2xx 必须 throw：静默 break 会把已经读到的题一起丢掉，" +
      "题库凭空少一半，界面上还看不出少了"
  );
});

it("D1：经典页不得再用「失败和读到头了合并成一句 break」的写法", () => {
  assert.ok(
    !/if\s*\(\s*!more\.ok\s*\|\|\s*!page\.items\.length\s*\)\s*break/.test(library),
    "仍有 if (!more.ok || !page.items.length) break：失败和正常结束混成一条路，" +
      "失败会被当成「读完了」"
  );
});

it("D2：Eagle 单份资料加载失败必须记下失败原因，不能静默 break", () => {
  const doc = bodyOf(eagle, "async function ensureDocumentQuestionsLoaded");
  assert.ok(doc, "找不到 ensureDocumentQuestionsLoaded");
  assert.ok(
    /loadError\s*=/.test(doc),
    "翻页遇到非 2xx 必须记下 loadError：直接 break 之后界面还会写" +
      "「已入库 N 题已在列表中」，等于替失败作证"
  );
  assert.ok(
    !/if\s*\(\s*!res\.ok\s*\)\s*break/.test(doc),
    "仍有 if (!res.ok) break：这一份资料的题会静默少掉，界面还宣称全部载入"
  );
});

it("D2：Eagle 必须明确说「没有载入完」，并说清已经取到多少", () => {
  const block = blockOf(eagle, "if (loadError)");
  assert.ok(block, "找不到 if (loadError) 分支：失败原因记下了却没人说");
  assert.ok(
    block.includes("没有载入完"),
    "失败提示里必须写「没有载入完」：只说「没载入」分不清是没载入还是本来就没有"
  );
  assert.ok(
    /incoming\.length/.test(block),
    "失败提示里必须带上已取到的数量：只说失败不说取到多少，等于让用户重头再来"
  );
});

it("D2：Eagle 加载失败必须弹 toast，不能只在列表底部留一句", () => {
  const block = blockOf(eagle, "if (loadError)");
  assert.ok(block, "找不到 if (loadError) 分支");
  assert.ok(
    /showToast\s*\(\s*tail\s*,\s*['"]error['"]\s*\)/.test(block),
    "失败必须弹错误 toast：只写在列表底部，滚动一下就看不见了"
  );
});

it("D3：解析编辑器读 AI 初稿失败必须抛错，不能 return null", () => {
  const draft = bodyOf(sol, "async function fetchAiDraft");
  assert.ok(draft, "找不到 fetchAiDraft");
  assert.ok(
    /if\s*\(\s*!res\.ok\s*\)\s*throw/.test(draft),
    "读初稿遇到非 2xx 必须 throw：return null 会和「确实没有初稿」混成一条路"
  );
  assert.ok(
    !/if\s*\(\s*!res\.ok\s*\)\s*return\s+null/.test(draft),
    "仍有 if (!res.ok) return null：读失败会被显示成「没有初稿」"
  );
});

it("D3：读初稿失败必须把原因带到列状态里", () => {
  const loadCol = bodyOf(sol, "async function loadColumn");
  assert.ok(loadCol, "找不到 loadColumn");
  assert.ok(
    /col\.aiDraftError\s*=/.test(loadCol),
    "loadColumn 必须把读取失败记到 col.aiDraftError，否则渲染层无从区分「读不到」"
  );
  assert.ok(
    /draft\.error/.test(loadCol),
    "loadColumn 必须把 fetchAiDraft 返回的错误写进 col.aiDraftError"
  );
});

it("D3：渲染必须把「读不到」和「没有初稿」分开说", () => {
  const html = bodyOf(sol, "function columnHtml");
  assert.ok(html, "找不到 columnHtml");
  assert.ok(
    /const aiBox\s*=\s*col\.aiDraftError\s*\?/.test(html),
    "AI 初稿那块必须先看 col.aiDraftError：读不到的时候不能显示「还没有 AI 初稿」"
  );
  assert.ok(
    html.includes("AI 初稿没读到"),
    "必须有「AI 初稿没读到」这句：老师看到「没有初稿」就会去排生成任务，白跑额度"
  );
  assert.ok(
    html.includes("还没有 AI 初稿"),
    "真正的空态文案要留着：确实没有初稿时还得这么写"
  );
});
