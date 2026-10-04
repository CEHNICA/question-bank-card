"use strict";

// 自动切题没切全时，试卷头部必须有一行直说的话和一个能立刻动手的按钮。
// 之前的样子是：状态“待你终审”，题卡两张，缺掉的 22 题 nowhere 可寻。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");

const render = source.slice(source.indexOf("  function renderSettingsTask()"),
  source.indexOf("  function closeSettingsThen(action)"));
const listener = source.slice(source.indexOf('  $("cutResultManual").addEventListener'),
  source.indexOf('  $("pageZoomFit").addEventListener'));
const clone = (value) => JSON.parse(JSON.stringify(value));

const paperWith = (cut, extra = {}) => ({
  id: "paper", parse_mode: "manual", status: "ready", pages: [{ page_idx: 0 }],
  notes: [], counts: {}, cut_result: cut, ...extra,
});

function harness(paper, options = {}) {
  const nodes = new Map(), openings = [], switches = [];
  const $ = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, open: false, hidden: false, disabled: false, textContent: "", events: {},
      addEventListener(name, callback) { this.events[name] = callback; }, replaceChildren() {},
      close() { this.open = false; },
    });
    return nodes.get(id);
  };
  const context = {
    pageOpenIntent: 0, AbortController, manualSwitches: new Set(), aiCutContinuations: new Set(),
    state: { paperId: "paper", paper, papers: [], questions: [] },
    QBProgress: App, ACTIVE_STATUS: new Set(["queued", "parsing", "segmenting", "reading"]),
    newUploadReadContinuations: new Set(), $, el: () => ({}),
    paperReadSubmissionPending: () => false, paperSummary: () => "",
    syncTrashControls() {}, suggestedSplitGroups: () => [],
    renderCutReadingStage() {}, closePageDialog() {}, toast() {},
    updatePaperFromResponse() {}, refreshPaper: async () => true,
    QBRegionWait: { boundedRequest: async (task) => task(undefined) },
    api: async () => { throw new Error("no request expected"); },
    closeSettingsThen: (callback) => callback(),
    openPageDialog: (...args) => openings.push(clone(args)),
    switchToManual: async () => { switches.push("manual"); return options.switched !== false; },
  };
  vm.runInNewContext(render + listener + "\nglobalThis.render = renderSettingsTask;", context);
  return { context, $, nodes, openings, switches };
}

(async () => {
  assert.match(html, /id="cutResultBanner"/);
  assert.ok(html.indexOf('id="paperStatus"') < html.indexOf('id="cutResultBanner"'),
    "the banner sits under the status line, not buried in the menu");

  // 切全了的卷子不占地方。
  const clean = harness(paperWith({ verdict: "complete" }));
  clean.context.render();
  assert.equal(clean.$("cutResultBanner").hidden, true);
  assert.equal(clean.$("cutResultMessage").textContent, "");

  // 缺一部分：点名缺哪几题，并给出补切入口。
  const degraded = harness(paperWith({
    verdict: "degraded", missing: [3, 4, 5], found: 2, expected: 5,
    message: "原卷上印着第 3、4、5 题的题号，但现在还没有对应的题卡。",
  }));
  degraded.context.render();
  assert.equal(degraded.$("cutResultBanner").hidden, false);
  assert.match(degraded.$("cutResultMessage").textContent, /3、4、5/);
  assert.equal(degraded.$("cutResultManual").textContent, "在原卷上补切（第 3、4、5 题）");

  // 一题没切出：不能说“待你终审”，按钮要去原卷上切题。
  const failed = harness(paperWith({
    verdict: "failed", found: 0, expected: 24, missing: [],
    message: "自动切题没有切出任何题目：本地文字层没有可靠题卡，已保留原页供手工切题。",
  }, { status: "failed" }));
  failed.context.render();
  assert.equal(failed.$("cutResultBanner").hidden, false);
  assert.match(failed.$("cutResultMessage").textContent, /没有切出任何题目/);
  assert.equal(failed.$("cutResultManual").textContent, "在原卷上切题");

  // 扫描件：没有依据判断全不全，话必须说“请核对”而不是“切好了”。
  const scan = harness(paperWith({
    verdict: "unverified", found: 2, expected: 0, missing: [],
    message: "这份资料没有可读取的文字，自动切题没有依据判断是否切全；请对照原卷核对题数。",
  }));
  scan.context.render();
  assert.equal(scan.$("cutResultBanner").hidden, false);
  assert.equal(scan.$("cutResultManual").textContent, "对照原卷核对题数");

  // 没有原页就没有可以点的动作，按钮不该出现。
  const noPages = harness(paperWith({ verdict: "failed", found: 0, expected: 6, missing: [],
    message: "自动切题没有切出任何题目。" }, { pages: [] }));
  noPages.context.render();
  assert.equal(noPages.$("cutResultManual").hidden, true);

  // 一点就进框题，不用先去试卷操作菜单里找“改为手工切题”。
  degraded.$("cutResultManual").events.click();
  assert.equal(degraded.openings.length, 1);
  assert.deepEqual(degraded.openings[0], ["new"]);
  assert.equal(degraded.switches.length, 0, "a manual paper does not need switching again");

  // 还在失败状态：先转手工，再进框题。
  await failed.$("cutResultManual").events.click();
  assert.equal(failed.switches.length, 1);
  assert.deepEqual(failed.openings[0], ["new"]);

  // 转手工失败时不打开框题界面：没有准备好的原页，框了也存不下来。
  const blocked = harness(paperWith({ verdict: "failed", found: 0, expected: 6, missing: [],
    message: "自动切题没有切出任何题目。" }, { status: "failed" }), { switched: false });
  await blocked.$("cutResultManual").events.click();
  assert.equal(blocked.openings.length, 0);

  console.log("cut-result banner ok");
})();
