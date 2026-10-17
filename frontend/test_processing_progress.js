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

// All active stages, including chunked tasks, can stop locally.
assert.match(indexHtml, /<button id="settingsStop" class="link-button" type="button" hidden/);
assert.match(reparseSource, /api\(`\/api\/papers\/\$\{state\.paperId\}\/stop`, \{ method: "POST", body: \{\} \}\)/);
for (const status of ["queued", "parsing", "segmenting", "reading"]) {
  assert.equal(Progress.canStopPaper({ status }), true);
  assert.equal(Progress.canStopPaper({ status, processing: { chunks: { total: 3 } } }), true);
}
for (const status of ["ready", "failed", "needs_grouping", ""]) assert.equal(Progress.canStopPaper({ status }), false);
assert.equal(Progress.canStopPaper(null), false);
assert.match(reparseSource, /const stoppable = QBProgress\.canStopPaper\(paper\);/);
assert.match(reparseSource, /先点上面的“停止处理”，停下来以后就能删除。/);
assert.match(reparseSource, /paper\.stopped \? "已停止" : "处理失败"/);

// 试卷列表的进度条只有两种状态：不用再看和还要看。两个数由后端按审核页那套
// 判据算好后下发，列表和「N 张要看」都直接用它，不在这里另数一遍。
assert.deepEqual(Progress.paperProgress({ total: 25, done: 0, todo: 25, green: 21, yellow: 4, red: 0, approved: 0, settled: 0 }),
  { total: 25, todo: 25, done: 0 }, "审核页说 25 张要看，列表就不能说 4 张");
assert.deepEqual(Progress.paperProgress({ total: 25, done: 25, todo: 0, green: 0, yellow: 0, red: 0, approved: 25, settled: 25 }),
  { total: 25, todo: 0, done: 25 });
// 识读中的题两边都不算：谁都没看过。
assert.deepEqual(Progress.paperProgress({ total: 6, done: 0, todo: 2, waiting: 4, green: 0, yellow: 2, red: 0 }),
  { total: 6, todo: 2, done: 0 });
// 后端给了数就不许再用绿/黄/红推算，哪怕推出来的结果不一样。
assert.deepEqual(Progress.paperProgress({ total: 10, done: 10, todo: 0, green: 7, yellow: 3, red: 0 }),
  { total: 10, todo: 0, done: 10 });

// 老服务没有 done/todo 时才退回本地推算；下面几条走的是这条兜底路径。
assert.deepEqual(Progress.paperProgress({ total: 25, green: 18, yellow: 3, red: 1, approved: 3, settled: 0 }),
  { total: 25, todo: 4, done: 21 });
assert.deepEqual(Progress.paperProgress({ total: 9, green: 7, yellow: 2 }),
  { total: 9, todo: 2, done: 7 });
assert.deepEqual(Progress.paperProgress({ total: 25, green: 0, yellow: 0, red: 0, approved: 0, settled: 25, published: 25 }),
  { total: 25, todo: 0, done: 25 }, "全部已入库的卷子不该还画着一段“还要看”");
assert.deepEqual(Progress.paperProgress({ total: 10, green: 0, yellow: 0, approved: 0, settled: 10, published: 10 }),
  { total: 10, todo: 0, done: 10 }, "已入库 10 就不能同时说 3 张要看");
assert.deepEqual(Progress.paperProgress({ total: 4, waiting: 4 }), { total: 4, todo: 0, done: 0 });
assert.deepEqual(Progress.paperProgress(), { total: 0, todo: 0, done: 0 });
assert.deepEqual(Progress.paperProgress({ total: 6, green: 4, red: 2, yellow: 0 }),
  { total: 6, todo: 2, done: 4 }, "识读失败的题同样算还要看，不能因为没有黄题就少算");

// 1.25.11：卷名下面「还有 N 页没切出题目」和绿色完成条「全部 25 题已处理完」同屏出现。
// 两句话必须自己交代清楚关系，否则读的人只会看见「还有活」和「干完了」对不上。
assert.equal(Progress.manualPagesNote(4, 25),
  "原卷另有 4 页没切出题目，不在上面这 25 道题里。需要更多题时可从原卷补齐。");
assert.equal(Progress.manualPagesNote(1, 6),
  "原卷另有 1 页没切出题目，不在上面这 6 道题里。需要更多题时可从原卷补齐。");
// 一道题都还没切出来时不能说「不在上面这 0 道题里」。
assert.equal(Progress.manualPagesNote(4, 0), "原卷有 4 页没切出题目。需要更多题时可从原卷补齐。");
assert.equal(Progress.manualPagesNote(4, undefined), "原卷有 4 页没切出题目。需要更多题时可从原卷补齐。");
assert.equal(Progress.manualPagesNote(0, 25), "", "没有手动页就别占一行");
assert.equal(Progress.manualPagesNote(undefined, 25), "");
// 旧口径会让人以为和绿色完成条打架：只说「还有页没切出来」，不说这几页不在题里。
for (const note of [Progress.manualPagesNote(4, 25), Progress.manualPagesNote(4, 0)]) {
  assert.doesNotMatch(note, /尚未自动切出题目/, "「尚未自动切出题目」听着像还有活没干完");
  assert.doesNotMatch(note, /全部.*已处理完/, "完成的话交给绿色条说，别在这儿抢一遍");
}
// 页数必须真的写出来，不能丢成「有  页」。
assert.match(Progress.manualPagesNote(4, 25), /另有 4 页/, "页数要出现在句子里");
assert.match(Progress.manualPagesNote(3, 25), /另有 3 页/, "页数要跟着数走");
assert.match(Progress.manualPagesNote(4, 25), /这 25 道题/, "已入库的题数也要在句子里");

// 光测 manualPagesNote 不够：把这句话删掉、改回就地拼接，函数照样在那儿、上一批断言
// 全绿，而页面上又变回「还有页没切出来」。所以要钉住调用点本身。
assert.match(appSource, /const note = QBProgress\.manualPagesNote\(manualPages\.length, c\.all\);/,
  "这句要收在 manualPagesNote 里，别在渲染处就地拼");
assert.match(appSource, /if \(note\) statusText\.append\(el\("span", "processing-detail", note\)\);/,
  "空句子不该占一行");
assert.doesNotMatch(appSource, /尚未自动切出题目/,
  "旧口气「尚未自动切出题目」不许再回到页面上：它和下面的绿色完成条摆在一起像打架");

// 界面上真的只画两段。
const vm = require("node:vm");
const meterStart = appSource.indexOf("  function miniMeter(paper) {");
const meterEnd = appSource.indexOf("  function renderPaperList() {");
// indexOf 找不到时不报错，只会安静地切出别的片段（甚至空串），那样的门禁等于没有。
// 标记对不上就直接停下，别让「少了一个函数」这件事拖到那个函数真被调用才炸。
assert.ok(meterStart > -1 && meterEnd > meterStart, "切不出 miniMeter：app.js 里的函数名或位置变了");
const meterSource = appSource.slice(meterStart, meterEnd);
assert.ok(meterSource.includes("function miniMeter(paper) {"), "切出来的片段里没有 miniMeter，断言会空跑");
assert.ok(meterSource.includes("function renderPaperList() {") === false, "切片多带了一段别的函数进来");
const meterNode = () => ({ dataset: {}, style: {}, children: [], append(...items) { this.children.push(...items); } });
const meterContext = {
  QBProgress: Progress,
  el: (tag, className) => Object.assign(meterNode(), { className })
};
vm.runInNewContext(`${meterSource}\nthis.meter = miniMeter;`, meterContext);
const drawn = (counts) => {
  const bar = meterContext.meter({ counts });
  return { title: bar.title, parts: bar.children.map((part) => [part.dataset.state, part.style.width, part.style.background]) };
};
// 1.25.11：细线的悬停说明必须用屏幕上的词。卷名那行写「已入库 25 · 22 张要看」，
// 悬停再改口叫「不用再看 25」的话，同一个数字隔十几像素换了名字，人只会以为算错了。
assert.deepEqual(drawn({ total: 25, done: 0, todo: 25, green: 21, yellow: 4, red: 0, approved: 0, settled: 0 }),
  { title: "这条细线是这份卷子的进度：橙色 25 题还要看。", parts: [["todo", "100%", "var(--amber-bar)"]] });
assert.deepEqual(drawn({ total: 10, done: 10, todo: 0, green: 0, yellow: 0, approved: 0, settled: 10 }),
  { title: "这条细线是这份卷子的进度：绿色 10 题都处理完了，没有还要看的题。", parts: [["done", "100%", "var(--green-bar)"]] });
assert.deepEqual(drawn({ total: 25, done: 9, todo: 16, green: 6, yellow: 13, red: 0, approved: 4, settled: 5 }),
  { title: "这条细线是这份卷子的进度：绿色 9 题已经处理完，橙色 16 题还要看。",
    parts: [["done", "36%", "var(--green-bar)"], ["todo", "64%", "var(--amber-bar)"]] });
// 「不用再看」是内部口径，页面上从来没写过这四个字；留着就等于同一件事两套说法。
// 断的是真正显示出去的那串 title，不是源码文本：源码注释里要留着这句话交代来历，
// 拿注释去断，等于逼着后人把历史抹掉才让测试变绿。
for (const counts of [
  { total: 25, done: 0, todo: 25, green: 21, yellow: 4, approved: 0, settled: 0 },
  { total: 10, done: 10, todo: 0, green: 0, yellow: 0, approved: 0, settled: 10 },
  { total: 25, done: 9, todo: 16, green: 6, yellow: 13, red: 0, approved: 4, settled: 5 }
]) {
  assert.doesNotMatch(drawn(counts).title, /不用再看/, "细线的悬停说明不许再出现「不用再看」");
}
assert.doesNotMatch(appSource, /题不用再看（/, "进度数字的悬停说明也不许再出现「不用再看」");
assert.equal(meterContext.meter({ counts: { total: 4, done: 0, todo: 0, waiting: 4 } }), null,
  "全部还在识读时不画绿条：上面那行文字已经说清这个状态了");
// 1.12.7：没有题数时什么都不画。空壳 .mini-meter 也会画出自己的 3px 灰轨，
// 失败的卷在列表里就多一根像坏掉的进度条。
assert.equal(meterContext.meter({ counts: { total: 0 } }), null, "没有题数的试卷不画条");
assert.equal(meterContext.meter({}), null, "没有题数的试卷不画条，也不报错");
assert.doesNotMatch(meterSource, /--red\)/, "识读失败不再单独占一段颜色");
assert.doesNotMatch(meterSource, /已通过 \$\{/, "绿色那段不是“已通过”：已入库但没打勾的题一道都没有");
