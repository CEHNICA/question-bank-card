"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const QBApi = require("./qb-api-client.js");
const QBIntakeStaging = require("./intake-staging.js");
const QBIntakeSplitCanvas = require("./intake-split-canvas.js");

test("QBApi 统一注入安全头、解析结构化错误并串行执行同资源写队列", async () => {
  const seen = [];
  const res = await QBApi.request("/api/test", {
    method: "POST",
    body: { hello: "world" },
    fetchImpl: async (url, options) => {
      seen.push({ url, options });
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      };
    },
  });
  assert.deepEqual(res, { ok: true });
  assert.equal(seen[0].options.headers["X-QB-Request"], "1");
  assert.equal(seen[0].options.headers["Content-Type"], "application/json");
  assert.equal(seen[0].options.body, JSON.stringify({ hello: "world" }));

  await assert.rejects(
    () => QBApi.request("/api/conflict", {
      method: "POST",
      body: {},
      fetchImpl: async () => ({
        ok: false,
        status: 409,
        json: async () => ({ error: "版本已更新，请刷新" }),
      }),
    }),
    (err) => err.status === 409 && err.code === "CONFLICT" && err.message === "版本已更新，请刷新"
  );

  const queue = QBApi.createMutationQueue();
  const trace = [];
  const p1 = queue.enqueue("paper:p1", async () => {
    trace.push("start-1");
    await new Promise((r) => setTimeout(r, 20));
    trace.push("end-1");
    return 1;
  });
  const p2 = queue.enqueue("paper:p1", async () => {
    trace.push("start-2");
    trace.push("end-2");
    return 2;
  });
  const results = await Promise.all([p1, p2]);
  assert.deepEqual(results, [1, 2]);
  assert.deepEqual(trace, ["start-1", "end-1", "start-2", "end-2"]);
});

test("QBIntakeStaging 自然序排页、旋转归一化与摘要生成", () => {
  const sorted = QBIntakeStaging.sortStagingEntries([
    { file: { name: "page-10.jpg" } },
    { file: { name: "page-2.jpg" } },
    { file: { name: "page-1.jpg" } },
  ]);
  assert.deepEqual(sorted.map((item) => item.file.name), ["page-1.jpg", "page-2.jpg", "page-10.jpg"]);
  assert.equal(QBIntakeStaging.normalizeTurns(5), 1);
  assert.equal(QBIntakeStaging.normalizeTurns(-1), 3);

  const summary = QBIntakeStaging.buildStagingSummary({
    files: [{ size: 1536 }, { size: 512 }],
    materialType: "exam",
    enhance: true,
    cloudReady: true,
    allowCloud: false,
  });
  assert.equal(summary.countText, "共 2 页照片（2.0 KB）");
  assert.match(summary.detailText, /开启扫描增亮/);
  assert.match(summary.detailText, /仅保留在本机处理/);
});

test("QBIntakeSplitCanvas 坐标换算、页框叠加与三步进度条状态", () => {
  assert.deepEqual(QBIntakeSplitCanvas.boxRectPercent([100, 200, 500, 400]), {
    left: 10,
    top: 20,
    width: 40,
    height: 20,
  });
  assert.equal(QBIntakeSplitCanvas.boxRectPercent([500, 200, 500, 400]), null);

  const boxes = QBIntakeSplitCanvas.pageBoxesForPage([
    { id: 11, number: 1, approved: true, regions: [{ page_idx: 0, bbox: [10, 20, 300, 120] }] },
    { id: 12, number: 2, approved: false, state: "reading_failed", regions: [{ page_idx: 0, bbox: [10, 140, 300, 260] }] },
    { id: 13, number: 3, approved: false, regions: [{ page_idx: 1, bbox: [10, 20, 300, 120] }] },
  ], 0, 11);
  assert.equal(boxes.length, 2);
  assert.equal(boxes[0].tone, "active");
  assert.equal(boxes[1].tone, "attention");

  const s1 = QBIntakeSplitCanvas.computeStepperState({ status: "parsing", pages: [{ page_idx: 0 }] }, { all: 0, approved: 0 });
  assert.equal(s1.activeStep, 1);
  const s2 = QBIntakeSplitCanvas.computeStepperState({ status: "ready", pages: [{ page_idx: 0 }] }, { all: 5, approved: 2 });
  assert.equal(s2.activeStep, 2);
  const s3 = QBIntakeSplitCanvas.computeStepperState({ status: "ready", pages: [{ page_idx: 0 }] }, { all: 5, approved: 5 });
  assert.equal(s3.activeStep, 3);
});

test("2.1.0 前后端集成契约：三步条、分屏原卷、回收站安全撤回与 Eagle 直达解析", () => {
  const indexHtml = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

  assert.ok(indexHtml.indexOf('src="/qb-api-client.js"') < indexHtml.indexOf('src="/app.js"'));
  assert.ok(indexHtml.indexOf('src="/intake-staging.js"') < indexHtml.indexOf('src="/app.js"'));
  assert.ok(indexHtml.indexOf('src="/intake-split-canvas.js"') < indexHtml.indexOf('src="/app.js"'));
  for (const id of ["intakeStepper", "paperWorkspace", "intakeSplitPane", "settingsTrashPaper", "photoStagingBar", "photoAllowCloud"]) {
    assert.match(indexHtml, new RegExp(`id="${id}"`), `index.html 缺少 #${id}`);
  }
  assert.match(appJs, /async function moveCurrentPaperToTrash\(\)/);
  assert.match(appJs, /edit_solution=1/);
  assert.match(eagleHtml, /src="\/qb-api-client\.js"/);
  assert.match(eagleHtml, /edit_solution.*openSolutionEditorForId/s);
  const splitJs = fs.readFileSync(path.join(__dirname, "intake-split-canvas.js"), "utf8");
  assert.match(splitJs, /\/api\/papers\/\$\{paper\.id\}\/pages\/\$\{pageIdx\}\/preview/);

  // 2.1.1：高分屏高清图标与帮助系统同步更新
  const libraryHtml = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
  assert.match(indexHtml, /class="welcome-mark" src="\/app-icon-256\.png"/);
  assert.match(libraryHtml, /class="brand-mark" src="\/app-icon-256\.png"/);
  assert.match(indexHtml, /左右分屏对照原卷与双向定位/);
  assert.match(indexHtml, /拍照暂存旋转调序与批量识读续读/);
  assert.match(eagleHtml, /id="eagleHelpModal"/);
  assert.match(eagleHtml, /openEagleHelpModal\(/);

  // 0 题待切状态：隐藏筛选条与重复步骤条、允许错误按钮换行避免溢出
  const stylesCss = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
  assert.match(stylesCss, /\.cut-reading-steps\[hidden\] \{ display: none; \}/);
  assert.match(stylesCss, /\.toolbar\[hidden\] \{ display: none; \}/);
  assert.match(stylesCss, /\.error-actions \{[^}]*flex-wrap:\s*wrap/);
  assert.match(appJs, /开始切题与校正/);
});
