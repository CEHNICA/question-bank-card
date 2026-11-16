"use strict";
// Eagle 右栏「读不到被当成没有 / 还在跑」的守护测试（第十三轮）。
//
// 这一轮实测出来的真缺陷（Playwright 真实浏览器 + 故障注入，逐条对照过）：
//
// D4 任务状态读取（frontend/eagle/index.html）
//   旧版 fetchLibraryJobs 读失败一律 return []，
//   轮询循环拿到空数组就 continue，一路空转到 5 分钟超时，
//   最后对外说「AI 还在后台跑（超过 5 分钟没回）」—— 那是拿「读不到」冒充「还在跑」。
//   修好后：失败带上原因，轮询立刻停下并说「读不到 AI 任务状态」。
//
// D5 单题详情读取
//   旧版 refreshQuestionSolution 失败只 console.warn，题目标上不留任何痕迹，
//   右栏于是显示「暂无待采纳建议」—— 读失败被说成了「没有建议」。
//   修好后：q.detailReadError 记下原因，右栏显示「这道题的详情没读到…」。
//
// D6 快速审题徽标与空态
//   旧版：详情没读到时徽标写「○ 暂无解析」，空态块写「这道题还没有答案与解析」。
//   修好后：徽标写「⚠ 解析没读到」，空态块写「这道题的解析没读到」并给「重新读取」。
//
// D7 标签目录读取
//   旧版 reloadTagCatalogue 失败 return null，调用方照旧弹「已给 N 道题贴上标签」成功提示，
//   目录其实还是旧的；首屏标签目录读失败更是静默跳过，标签树空着像「本来就没标签」。
//   修好后：tagCatalogueReadError 记下原因，贴标成功但目录没刷新时如实说明。
//
// 静态源码断言：喂修复前的文件进来必须变红（--eagle=）。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
}

const eaglePath = arg("eagle") || path.join(__dirname, "eagle", "index.html");
const eagle = fs.readFileSync(eaglePath, "utf8");

/** 取某个块（花括号配平），按关键词定位起点。 */
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
 * 直接找第一个 "{" 会在 `async function load({ append = false } = {})`
 * 这种写法上抓错地方 —— 抓到解构的 "{"，函数体根本没进来，断言会假红。
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

it("尺子自检：六处关键函数必须找得到（否则下面的断言会变成恒真）", () => {
  const jobs = bodyOf(eagle, "async function fetchLibraryJobs");
  assert.ok(jobs, "找不到 fetchLibraryJobs（结构变了？）");
  assert.ok(jobs.includes("/api/library/jobs?"), "任务状态接口 URL 变了");

  const poll = bodyOf(eagle, "async function pollTagSuggestionUntilSettled");
  assert.ok(poll, "找不到 pollTagSuggestionUntilSettled");

  const re = bodyOf(eagle, "async function requestAiReAnalysis");
  assert.ok(re, "找不到 requestAiReAnalysis");

  const refresh = bodyOf(eagle, "async function refreshQuestionSolution");
  assert.ok(refresh, "找不到 refreshQuestionSolution");

  const fromServer = bodyOf(eagle, "async function refreshQuestionFromServer");
  assert.ok(fromServer, "找不到 refreshQuestionFromServer");

  const inspector = bodyOf(eagle, "function renderRightInspector");
  assert.ok(inspector, "找不到 renderRightInspector");
  assert.ok(inspector.includes("aiSuggestedTagsContainer"), "右栏 AI 建议容器变了");

  const quick = bodyOf(eagle, "function renderQuickLookSolution");
  assert.ok(quick, "找不到 renderQuickLookSolution");

  const catalogue = bodyOf(eagle, "async function reloadTagCatalogue");
  assert.ok(catalogue, "找不到 reloadTagCatalogue");
});

it("D4：任务状态读取失败必须带出原因，不能拿空数组冒充「没有任务」", () => {
  const jobs = bodyOf(eagle, "async function fetchLibraryJobs");
  assert.ok(jobs, "找不到 fetchLibraryJobs");
  assert.ok(
    /error\s*:/.test(jobs),
    "fetchLibraryJobs 失败必须把原因放进返回值：空数组会被轮询当成「没有任务」，" +
      "然后一路空转到超时，最后谎报「还在后台跑」"
  );
  assert.ok(
    !/return\s*\[\s*\]/.test(jobs),
    "仍有裸 return []：读失败和「真的没有任务」混成一条路"
  );
  assert.ok(
    !/if\s*\(\s*!res\.ok\s*\)\s*return\s+/.test(jobs),
    "仍有 if (!res.ok) return ...：非 2xx 必须带原因回去"
  );
});

it("D4：标签建议轮询遇到读失败必须停下并告诉用户，不能空转", () => {
  const poll = bodyOf(eagle, "async function pollTagSuggestionUntilSettled");
  assert.ok(poll, "找不到 pollTagSuggestionUntilSettled");
  assert.ok(
    /jobReadError/.test(poll),
    "轮询必须接住 fetchLibraryJobs 带回来的错误原因"
  );
  assert.ok(
    /if\s*\(\s*jobReadError\s*\)/.test(poll),
    "读失败必须单独分支处理：不能和「没有任务」走同一条路"
  );
  assert.ok(
    /showToast\s*\(/.test(poll) && /读不到/.test(poll),
    "读失败必须弹提示告诉用户读不到，别让他干等"
  );
});

it("D4：生成轮询读失败必须立刻中止，不得用「还在后台跑」顶替", () => {
  const re = bodyOf(eagle, "async function requestAiReAnalysis");
  assert.ok(re, "找不到 requestAiReAnalysis");
  assert.ok(
    /jobReadError/.test(re),
    "生成轮询必须接住任务状态读取的错误原因"
  );
  assert.ok(
    re.includes("读不到 AI 任务状态"),
    "必须有「读不到 AI 任务状态」这句：读不到的时候还说「还在后台跑」，" +
      "等于让用户对着一个不知道状态的进程干等"
  );
  assert.ok(
    re.includes("AI 还在后台跑"),
    "真正的超时分支「还在后台跑」要留着：确实读得到、只是没回完时还得这么写"
  );
  const readErrIdx = re.indexOf("读不到 AI 任务状态");
  const runningIdx = re.indexOf("AI 还在后台跑");
  assert.ok(readErrIdx >= 0 && runningIdx >= 0, "两句都要在");
  assert.ok(
    readErrIdx < runningIdx || /jobReadError[\s\S]*?if\s*\(/.test(re),
    "读不到的错误分支必须能独立成立，不能被超时分支覆盖"
  );
});

it("D5：单题详情读失败必须记到题目上（detailReadError）", () => {
  const refresh = bodyOf(eagle, "async function refreshQuestionSolution");
  assert.ok(refresh, "找不到 refreshQuestionSolution");
  assert.ok(
    /q\.detailReadError\s*=/.test(refresh),
    "refreshQuestionSolution 失败必须写 q.detailReadError：渲染层才能区分「读不到」"
  );
  assert.ok(
    /detailReadError\s*=\s*['"]{2}/.test(refresh),
    "读成功必须把 q.detailReadError 清空：旧的失败原因不能赖着不走"
  );
  assert.ok(
    !/if\s*\(\s*!res\.ok\s*\)\s*return\s+null\s*;/.test(refresh),
    "仍有 if (!res.ok) return null：失败原因会被吞掉，右栏只能显示「没有建议」"
  );
});

it("D5：贴标后刷新题目标签读失败也必须记原因", () => {
  const fromServer = bodyOf(eagle, "async function refreshQuestionFromServer");
  assert.ok(fromServer, "找不到 refreshQuestionFromServer");
  assert.ok(
    /q\.detailReadError\s*=/.test(fromServer),
    "refreshQuestionFromServer 失败必须写 q.detailReadError：否则贴标成功但标签没更新，" +
      "界面还保持旧样子"
  );
});

it("D5：右栏必须先说「读不到」再说「没有建议」，并给一个重新读取的入口", () => {
  const inspector = bodyOf(eagle, "function renderRightInspector");
  assert.ok(inspector, "找不到 renderRightInspector");
  const errIdx = inspector.indexOf("detailReadError");
  const emptyIdx = inspector.indexOf("暂无待采纳建议");
  assert.ok(errIdx >= 0, "右栏必须处理 detailReadError");
  assert.ok(emptyIdx >= 0, "右栏的空态文案要留着");
  assert.ok(
    errIdx < emptyIdx,
    "「读不到」的分支必须排在「暂无待采纳建议」前面：顺序反了老师还是会以为这题没建议"
  );
  assert.ok(
    inspector.includes("读不到"),
    "右栏必须写清楚是「读不到」：只写「没有建议」等于拿失败冒充结论"
  );
  assert.ok(
    /retryReadQuestionDetail/.test(inspector),
    "右栏详情读失败时必须给「重新读取」入口：不然用户只能干等着"
  );
  assert.ok(
    bodyOf(eagle, "async function retryReadQuestionDetail"),
    "找不到 retryReadQuestionDetail：按钮点了没反应等于没有入口"
  );
});

it("D6：快速审题徽标在详情读失败时必须写「解析没读到」", () => {
  const quick = bodyOf(eagle, "function renderQuickLookSolution");
  assert.ok(quick, "找不到 renderQuickLookSolution");
  const errIdx = quick.indexOf("q.detailReadError");
  const noneIdx = quick.indexOf("○ 暂无解析");
  assert.ok(errIdx >= 0, "徽标必须处理 q.detailReadError");
  assert.ok(noneIdx >= 0, "「○ 暂无解析」这个真空态要留着");
  assert.ok(
    errIdx < noneIdx,
    "「解析没读到」的徽标必须排在「○ 暂无解析」前面：顺序反了读失败仍然被显示成没有解析"
  );
  assert.ok(quick.includes("解析没读到"), "徽标必须写「解析没读到」");
});

it("D6：快速审题的空态块必须区分「没读到」和「没有解析」", () => {
  const quick = bodyOf(eagle, "function renderQuickLookSolution");
  assert.ok(quick, "找不到 renderQuickLookSolution");
  assert.ok(
    /readFailed/.test(quick),
    "空态块必须按 detailReadError 分成两种说法"
  );
  assert.ok(
    quick.includes("这道题的解析没读到"),
    "必须有「这道题的解析没读到」：以前一律写「还没有答案与解析」，把读失败说成了没有内容"
  );
  assert.ok(
    quick.includes("这道题还没有答案与解析"),
    "真空态文案要留着：确实没录入解析时还得这么写"
  );
});

it("D7：标签目录读失败必须记原因，不能静默当空目录", () => {
  const catalogue = bodyOf(eagle, "async function reloadTagCatalogue");
  assert.ok(catalogue, "找不到 reloadTagCatalogue");
  assert.ok(
    /tagCatalogueReadError\s*=/.test(catalogue),
    "reloadTagCatalogue 失败必须写 tagCatalogueReadError：调用方要靠它说明「目录没刷新」"
  );
  assert.ok(
    !/if\s*\(\s*!res\.ok\s*\)\s*return\s+null\s*;/.test(catalogue),
    "仍有 if (!res.ok) return null：失败原因被吞掉，调用方只能当刷新成功"
  );
});

it("D7：标签目录读失败必须自己告诉用户，不能静默咽下去", () => {
  const catalogue = bodyOf(eagle, "async function reloadTagCatalogue");
  assert.ok(catalogue, "找不到 reloadTagCatalogue");
  assert.ok(
    /showToast\s*\(/.test(catalogue),
    "reloadTagCatalogue 失败必须自己弹提示：调用方一个都不检查返回值，" +
      "静默 return null 等于谁都不知道目录没刷新"
  );
  assert.ok(
    catalogue.includes("标签目录没读到"),
    "提示必须写明「标签目录没读到」：否则用户看到空标签树会以为这库本来就没标签"
  );
});

it("D7：右栏置顶标签区在目录读失败时必须说明「读不到」", () => {
  const inspector = bodyOf(eagle, "function renderRightInspector");
  assert.ok(inspector, "找不到 renderRightInspector");
  assert.ok(
    /tagCatalogueReadError/.test(inspector),
    "右栏置顶标签区必须处理目录读取失败：空着会被当成「没有置顶标签」"
  );
  assert.ok(
    inspector.includes("没有置顶标签"),
    "必须写明「不是没有置顶标签」：老师看到空区会以为置顶丢了"
  );
});
