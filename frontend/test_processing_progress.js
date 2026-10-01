"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Progress = require("./app.js");

assert.equal(Progress.formatDuration(568), "9分28秒");
assert.equal(Progress.formatAge(2), "刚刚");
assert.equal(Progress.formatAge(62), "1分2秒前");

const queued = Progress.processingPresentation({
  status: "queued",
  processing: { stage: "queued", queue_ahead: 2, elapsed_seconds: 192, idle_seconds: 2 }
});
assert.equal(queued.headline, "排队中 · 前面还有 2 项任务");
assert.match(queued.detail, /任务创建至今 3分12秒/);
assert.match(queued.detail, /本任务状态最近更新 刚刚/);
assert.equal(queued.determinate, false);
const longQueued = Progress.processingPresentation({
  status: "queued",
  processing: { stage: "queued", queue_ahead: 1, elapsed_seconds: 900, idle_seconds: 600 }
});
assert.equal(longQueued.stale, "", "排队不更新自身状态时不应误报后台停滞");

const parsedAhead = Progress.processingPresentation({
  status: "segmenting",
  processing: { stage: "segmenting", parsed_ahead: true, queue_ahead: 2, elapsed_seconds: 400, idle_seconds: 380 }
});
assert.equal(parsedAhead.headline, "MinerU 已解析完 · 等前面 2 项任务读完");
assert.equal(parsedAhead.stale, "", "提前解析完等待读题时不应误报后台停滞");

const chunks = Progress.processingPresentation({
  status: "parsing",
  processing: {
    stage: "parsing", determinate: true, completed: 2, total: 3,
    elapsed_seconds: 568, idle_seconds: 7,
    chunks: { active_ranges: [{ page_start: 201, page_end: 270 }] }
  }
});
assert.equal(chunks.headline, "MinerU 解析中 · 已完成 2/3 个分片");
assert.match(chunks.detail, /正在处理第 201–270 页/);
assert.equal(chunks.ratio, 2 / 3);

const singleMineru = Progress.processingPresentation({
  status: "parsing",
  processing: { stage: "parsing", determinate: false, elapsed_seconds: 20, idle_seconds: 5 }
});
assert.equal(singleMineru.determinate, false);
assert.match(singleMineru.detail, /MinerU 没有提供完成百分比/);

// 1.10.6: MinerU's own state, when the worker has heard it.
const mineruQueue = Progress.processingPresentation({
  status: "parsing",
  processing: { stage: "parsing", determinate: false, elapsed_seconds: 200, idle_seconds: 2,
    mineru: { state: "pending", for_seconds: 130 } }
});
assert.equal(mineruQueue.headline, "在 MinerU 排队中 · 已等 2分10秒");
assert.match(mineruQueue.detail, /题有据没有卡住/);
assert.equal(mineruQueue.determinate, false);
const mineruPages = Progress.processingPresentation({
  status: "parsing",
  processing: { stage: "parsing", determinate: true, completed: 3, total: 4, unit: "page",
    elapsed_seconds: 260, idle_seconds: 1, mineru: { state: "running", for_seconds: 40, pages: 3, total_pages: 4 } }
});
assert.equal(mineruPages.headline, "MinerU 识别中 · 第 3/4 页");
assert.equal(mineruPages.ratio, 3 / 4);
assert.equal(Progress.processingPresentation({
  status: "parsing", processing: { stage: "parsing", mineru: { state: "converting", for_seconds: 3 } }
}).headline, "MinerU 识别完了，正在打包结果");

// 1.10.7/1.10.8: 重新交给 MinerU only where it can help — not while MinerU says it is queueing
// (that only moves the file to the back), nor for chunks or while the result is coming back.
assert.equal(mineruQueue.canReparse, false);
const reparseAfter = (state, seconds) => Progress.processingPresentation({
  status: "parsing", processing: { stage: "parsing", elapsed_seconds: seconds, mineru: state ? { state, for_seconds: seconds } : undefined }
}).canReparse;
assert.equal(reparseAfter("pending", 3600), false);
assert.equal(reparseAfter("waiting-file", 59), false);
assert.equal(reparseAfter("waiting-file", 60), true);
assert.equal(reparseAfter("running", 299), false);
assert.equal(reparseAfter("running", 300), true);
assert.equal(reparseAfter("", 90), true);
assert.equal(Progress.processingPresentation({
  status: "parsing", processing: { stage: "parsing", elapsed_seconds: 300, mineru: { state: "downloading", for_seconds: 90 } }
}).canReparse, false);
assert.equal(chunks.canReparse, false);
assert.equal(queued.canReparse, false);
const reparseSource = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
assert.match(reparseSource, /api\(`\/api\/papers\/\$\{state\.paperId\}\/reparse`, \{ method: "POST", body: \{\} \}\)/);
assert.match(reparseSource, /if \(processing\.canReparse\) \{/);
assert.match(indexHtml, /<button id="settingsReparse" class="link-button" type="button" hidden/);

const reading = Progress.processingPresentation({
  status: "reading",
  processing: {
    stage: "reading", determinate: true, completed: 40, total: 561,
    elapsed_seconds: 568, idle_seconds: 2
  }
});
assert.equal(reading.headline, "AI 读题中 · 已完成 40/561");
assert.match(reading.detail, /剩余 521 道/);
assert.match(reading.detail, /任务创建至今 9分28秒/);
assert.match(reading.detail, /本任务状态最近更新 刚刚/);
assert.doesNotMatch(reading.detail, /约还需/, "没有实测速度时不估计剩余时间");

const withPace = Progress.processingPresentation({
  status: "reading",
  processing: {
    stage: "reading", determinate: true, completed: 12, total: 30,
    elapsed_seconds: 80, idle_seconds: 1, eta_seconds: 95
  }
});
assert.match(withPace.detail, /按目前速度约还需 1分35秒/);

const stale = Progress.processingPresentation({
  status: "reading",
  processing: {
    stage: "reading", determinate: true, completed: 1, total: 6,
    elapsed_seconds: 500, idle_seconds: 241
  }
});
assert.match(stale.stale, /已有 4分1秒没有新的本任务状态更新/);
assert.match(stale.stale, /这不等同于失败/);

const appSource = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
assert.match(appSource, /state\.pollTimer = setTimeout\(refreshPaper, 5000\)/);
assert.doesNotMatch(`${appSource}\n${html}`, /一般一两分钟|一两分钟出题卡/);
assert.doesNotMatch(appSource, /paper\.status === "reading"[^;]+:\s*0\.08/);
assert.match(html, /id="processingStages"/);
assert.match(html, /aria-valuemin="0" aria-valuemax="100"/);
assert.match(appSource, /paper\.recoverable_pause/);
assert.match(appSource, /额度不足，已暂停/);
assert.match(appSource, /classList\.toggle\("paused"/);

console.log("truthful processing progress checks: OK");

// 1.10.8: 停止处理 for a queued paper or one waiting on MinerU, so it can be deleted.
assert.match(indexHtml, /<button id="settingsStop" class="link-button" type="button" hidden/);
assert.match(reparseSource, /api\(`\/api\/papers\/\$\{state\.paperId\}\/stop`, \{ method: "POST", body: \{\} \}\)/);
assert.match(reparseSource, /const stoppable = paper\.status === "queued" \|\| \(paper\.status === "parsing" && !paper\.processing\?\.chunks\);/);
assert.match(reparseSource, /先点上面的“停止处理”，停下来以后就能删除。/);
assert.match(reparseSource, /paper\.stopped \? "已停止" : "处理失败"/);
