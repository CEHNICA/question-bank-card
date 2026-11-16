"use strict";
// 首屏别再为了一个徽标数字，把整本回收站明细拉回来。
//
// 真库实测（8768）：
//   GET /api/library/trash          -> 331 KB / 稳态 50~110 ms
//   GET /api/library/trash?summary=1 -> 几十字节 / 1 ms
//
// Eagle 首屏只要左下角「🗑️ 回收站」徽标上的一个数字，那份明细（10 份资料 +
// 445 道下架题，带题干预览、资料名、页码）首屏一个字节都用不上 —— 却是首屏
// 网络里最大的一块，比 /api/library/tags 还大。
//
// 要求：首屏走摘要，明细等回收站面板真打开了再拉。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("Eagle 首屏不该把回收站明细全拉回来", async (t) => {
  await t.test("1. 必须有摘要模式的概念", () => {
    assert.ok(
      /summary=1/.test(html),
      "必须出现 ?summary=1 的摘要请求（首屏只要计数）"
    );
  });

  await t.test("2. 首屏加载不许调全量回收站", () => {
    // 首屏的真实加载路径是 loadRealData（loadInitialLibraryData 只是转调它）
    const fn = html.match(/async function loadRealData\([\s\S]{0,8000}?\n    \}/);
    assert.ok(fn, "应能找到 loadRealData");
    assert.ok(
      !/await refreshLibraryTrash\(\)/.test(fn[0]),
      "首屏加载里不许调 refreshLibraryTrash()（那是拉全量明细的）"
    );
    assert.ok(
      /refreshLibraryTrashSummary/.test(fn[0]),
      "首屏加载应该调 refreshLibraryTrashSummary()（只拉计数）"
    );
  });

  await t.test("3. 必须有摘要刷新函数", () => {
    const fn = html.match(/async function refreshLibraryTrashSummary\([\s\S]{0,1500}?\n    \}/);
    assert.ok(fn, "应能找到 refreshLibraryTrashSummary");
    assert.ok(
      /summary=1/.test(fn[0]),
      "refreshLibraryTrashSummary 必须请求 ?summary=1"
    );
  });

  await t.test("4. 回收站面板打开时必须拉全量明细", () => {
    const fn = html.match(/function openRecycleBinView\([\s\S]{0,2500}?\n    \}/);
    assert.ok(fn, "应能找到 openRecycleBinView");
    assert.ok(
      /refreshLibraryTrash\(\)/.test(fn[0]),
      "回收站面板打开时必须拉全量明细（面板要印明细行）"
    );
  });

  await t.test("5. 徽标计数不许依赖明细数组", () => {
    // trashTotalCount 以前靠 archivedPapers.length / withdrawnQuestions.length。
    // 摘要模式没有明细数组，徽标就永远是 0。
    const fn = html.match(/function trashTotalCount\([\s\S]{0,900}?\n    \}/);
    assert.ok(fn, "应能找到 trashTotalCount");
    assert.ok(
      /counts/.test(fn[0]),
      "trashTotalCount 必须用 counts 字段算总数，不能只靠明细数组长度"
    );
  });
});
