"use strict";

// 「首批先画 + 后台补拉」这套设计不许被改回「整库拉完才画」。
//
// 背景（8768，副本库 810 道题，实测）：
//   旧：GET /api/library?bulk=1&limit=1000&offset=0&slim=1
//       1.36 MB / 服务端 202 ms / JSON.parse + 渲染 143 ms
//       → 首张卡片出现要 820~1140 ms
//   新：GET /api/library?bulk=1&limit=150&offset=0&slim=1&sort=display
//       216 KB / 服务端 62 ms
//       → 剩下的后台补，首屏不等
//
// 这套设计要成立，靠三件事同时对：
//   1. 批次边界必须跟显示顺序一致 —— 后端加的 sort=display
//      （number, -published_at, -id）就是前端列表真正在用的顺序。
//      不对齐的话，补进来的题会挤到已画出来那批前面，屏幕上的卡片全乱。
//   2. 整库重载时，上一次的后台补拉必须作废（generation 守卫），
//      不然旧数据会混进新列表。
//   3. 后台补拉期间不许谎报「已呈现全部」。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

// 按行提取函数体：这些函数都在 IIFE 里，缩进 4 空格，
// 函数体结束那行是「    }」。正则按大括号配对会被嵌套的 {} 提前截断，
// 所以这里按缩进找结束行。
function fnBody(name) {
  const lines = html.split(/\r?\n/);
  const start = lines.findIndex((l) =>
    new RegExp(`^\\s*(async )?function ${name}\\(`).test(l)
  );
  assert.ok(start >= 0, `应能找到函数 ${name}`);
  const indent = lines[start].match(/^\s*/)[0].length;
  let end = -1;
  for (let i = start + 1; i < lines.length; i += 1) {
    if (new RegExp(`^\\s{${indent}}\\}\\s*$`).test(lines[i])) {
      end = i;
      break;
    }
  }
  assert.ok(end > start, `应能找到函数 ${name} 的结束`);
  return lines.slice(start, end + 1).join("\n");
}

test("Eagle 首屏先画首批、后台再补拉", async (t) => {
  await t.test("1. 首屏只取一批，不是整库", () => {
    assert.match(html, /const LIBRARY_FIRST_BATCH = \d+;/, "必须定义首批条数");
    const batch = Number(html.match(/const LIBRARY_FIRST_BATCH = (\d+);/)[1]);
    assert.ok(
      batch > 0 && batch < 1000,
      `首批条数应该在 1~999 之间，实际 ${batch}`
    );
    assert.match(
      html,
      /bulk=1&limit=\$\{LIBRARY_FIRST_BATCH\}&offset=0&slim=1&sort=display/,
      "首屏请求必须只取一批，并且带 sort=display"
    );
  });

  await t.test("2. 批次边界必须跟显示顺序对齐（sort=display）", () => {
    assert.match(
      html,
      /sort=display/,
      "所有取批请求都必须带 sort=display"
    );
    const urls = html.match(/api\/library\?bulk=1[^`'"\s]*/g) || [];
    assert.ok(urls.length >= 2, "至少要有首屏和后台补拉两次请求");
    for (const url of urls) {
      assert.match(url, /sort=display/, `取批请求必须带 sort=display：${url}`);
    }
  });

  await t.test("3. 整库重载时旧的后台补拉必须作废", () => {
    assert.match(html, /libraryLoadGeneration \+= 1;/, "重载时要递增 generation");
    const body = fnBody("loadRemainingLibraryItems");
    assert.match(
      body,
      /if \(generation !== libraryLoadGeneration\) return;/,
      "后台补拉必须检查 generation，旧批次直接丢弃"
    );
  });

  await t.test("4. 后台补拉期间不许谎报「已呈现全部」", () => {
    assert.match(
      html,
      /题库载入中…/,
      "后台补拉期间要显示载入中"
    );
    const chunk = fnBody("renderNextQuestionChunk");
    assert.match(
      chunk,
      /libraryBackgroundPending/,
      "渲染分块时必须知道后台还在补拉"
    );
    assert.match(
      chunk,
      /libraryEffectiveTotal\(\)/,
      "载入中提示要用整库口径的总题数"
    );
  });

  await t.test("5. 命中数在后台补拉期间按整库口径报，不许闪一下", () => {
    assert.match(
      html,
      /libraryBackgroundPending && pristine\) \? libraryTotalCount : filteredQuestions\.length/,
      "后台补拉期间且用户没改筛选时，命中数报整库总数"
    );
    assert.match(
      html,
      /const filterChanged = \(JSON\.stringify\(filterState\) !== libraryFilterSignature\);/,
      "要能判断用户中途有没有改筛选"
    );
  });

  await t.test("6. 用户中途改过筛选，补完必须重画一次", () => {
    const body = fnBody("refreshLibraryAfterBackgroundLoad");
    assert.match(body, /if \(filterChanged\) \{\s*applyFilters\(\);/, "筛选变过要重画");
    assert.match(body, /filteredQuestions = computeFilteredQuestions\(\);/, "没变也要重算命中集合");
  });

  await t.test("7. 筛选逻辑必须抽成可复用的函数", () => {
    assert.match(
      html,
      /function computeFilteredQuestions\(\)/,
      "过滤+排序必须抽成 computeFilteredQuestions"
    );
    assert.match(
      html,
      /filteredQuestions = computeFilteredQuestions\(\);/,
      "applyFilters 要用抽出来的那个函数"
    );
  });

  await t.test("8. AI 初稿回填要给首批和后台批都做一遍", () => {
    assert.match(
      html,
      /async function backfillAiAnswersFromJobs\(questions\)/,
      "AI 初稿回填必须抽成带参数的函数"
    );
    const body = fnBody("backfillAiAnswersFromJobs");
    assert.match(body, /api\/library\/jobs\?ids=/, "回填要查答案任务");
    assert.match(body, /JOBS_BATCH = 500/, "后端一次最多收 500 个 id，必须分批发");
  });

  await t.test("9. 命中数不许从 150 闪成 810", () => {
    // 这个 bug 真出现过：「后台还在补」的状态牌摆晚了，
    // 首屏第一帧的命中数报的是首批条数（150），
    // 补完之后再跳成整库题数（810）—— 数字在老师眼皮底下闪一下。
    // 判据：pending 标记必须排在 applyFilters() 前面。
    const screen = html.match(/libFirstRes\.ok[\s\S]{0,4000}?loadRemainingLibraryItems\(/);
    assert.ok(screen, "应能找到首屏处理批次");
    const body = screen[0];
    const pendingAt = body.indexOf("libraryBackgroundPending =");
    const applyAt = body.indexOf("applyFilters()");
    assert.ok(pendingAt >= 0, "首屏要先把「后台还在补」摆上");
    assert.ok(applyAt >= 0, "首屏要调 applyFilters 画卡");
    assert.ok(
      pendingAt < applyAt,
      "「后台还在补」必须排在 applyFilters() 前面，否则命中数先报 150 再跳 810"
    );
  });
});
