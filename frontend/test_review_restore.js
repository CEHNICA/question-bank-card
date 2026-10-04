"use strict";

// 1.12.6：顶栏去题库再点回「录入终审」是整页跳转，筛选 / 当前题 / 展开的卡片
// 原本只活在内存里，回来就回到默认位置。这里钉住两件事：
// ① 每个改变现场的地方都写一次；② 落地时按「URL 优先、其次上一段现场」读回。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const cut = (from, to) => source.slice(source.indexOf(from), source.indexOf(to));
const helpers = cut('  const REVIEW_STATE_KEY = "qb-review-state";', "  function el(tag, className, text)");
const setFilter = cut("  function setFilter(key)", "  function renderFilters(");
const setCurrent = cut("  function setCurrent(id, {", "  function markReading(");
const setExpanded = cut("  function setExpanded(id, on,", "  // J/K 跳到一张收起的题");

function harness(options = {}) {
  const store = new Map();
  const questions = options.questions || Array.from({ length: 8 }, (_, index) => ({ id: index + 1 }));
  const renders = [];
  const context = {
    state: { paperId: "paper-a", filter: "all", current: null, expanded: new Set(), autoExpanded: new Set(), questions },
    FILTERS: [{ key: "all" }, { key: "todo" }, { key: "approved" }, { key: "ai" }],
    window: { sessionStorage: {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)) } },
    document: { querySelector: () => null },
    questionById: (id) => questions.find((q) => q.id === id) || null,
    visible: options.visible || (() => true),
    renderPaper: () => renders.push(context.state.filter),
    teach: () => {}, cardNodes: () => [], markReading: () => {}
  };
  vm.runInNewContext(helpers + setFilter + setCurrent + setExpanded, context);
  return { context, store, renders, saved: () => JSON.parse(store.get("qb-review-state")) };
}

// 写入端：切筛选、跳题、展开/收起都要留下现场。
{
  const run = harness();
  run.context.setFilter("todo");
  assert.equal(run.context.state.filter, "todo");
  assert.equal(run.saved().filter, "todo", "切到「需要核查」要记住");

  run.context.setCurrent(7);
  assert.equal(run.saved().current, 7, "停在第 7 题要记住");

  run.context.setExpanded(3, true);
  assert.deepEqual(run.saved().expanded, [3], "展开的卡片要记住");
  run.context.setExpanded(3, false);
  assert.deepEqual(run.saved().expanded, []);
}

// 读回端：卷号、筛选、展开的卡、当前题都回来。
{
  const run = harness();
  const remember = { paperId: "paper-a", filter: "todo", current: 7, expanded: [3, 5], savedAt: Date.now() };
  run.context.restoreReviewState(remember);
  assert.equal(run.context.state.filter, "todo");
  assert.deepEqual([...run.context.state.expanded], [3, 5]);
  assert.equal(run.context.state.current, 7, "回到同一道题");
  assert.deepEqual(run.renders, ["todo"], "恢复筛选要重画一次列表");
}

// 当前题在当前筛选下看不见时退回无高亮，不报错也不跳到别的题。
{
  const run = harness({ visible: (q) => q.id % 2 === 0 });
  run.context.state.current = 3;
  run.context.restoreReviewState({ filter: "all", current: 7, expanded: [] });
  assert.equal(run.context.state.current, 3, "看不见就不动它，不换成别的题");
  assert.deepEqual(run.renders, ["all"]);
}

// 恢复的题已经不在了 / 认不出的筛选与展开值：照旧忽略，不能让恢复流程中断。
{
  const gone = harness();
  gone.context.restoreReviewState({ filter: "all", current: 4242, expanded: [3, 99] });
  assert.equal(gone.context.state.current, null);
  assert.deepEqual([...gone.context.state.expanded], [3], "已经不在的题不恢复展开");
}
{
  const bad = harness();
  bad.context.restoreReviewState({ filter: "not-a-filter", current: null, expanded: "not-an-array" });
  assert.equal(bad.context.state.filter, "all");
  assert.equal(bad.context.state.expanded.size, 0);
}

// 换卷：现场跟着换到新卷，不能留着旧卷的筛选。
{
  const selectPaper = cut("  async function selectPaper(id)", "  async function clearPaperSelection()");
  assert.match(selectPaper, /state\.filter = "all";[\s\S]*?state\.current = null;[\s\S]*?saveReviewState\(\);/,
    "换卷后写下的必须是新卷的默认现场，否则下次进来会跳到别人的筛选");
}

// 落地顺序：URL 里的 paper 优先，其次上一段现场，最后才第一份卷。
{
  const start = cut("    await loadPapers();\n    const params", "    loadTeaching();");
  assert.ok(start.indexOf("if (wantedAvailable)") < start.indexOf("readReviewState()"),
    "?paper= 仍然优先，分享链接和书签不能被旧现场覆盖");
  assert.match(start, /const saved = readReviewState\(\);[\s\S]*?await selectPaper\(saved\.paperId\);\s*restoreReviewState\(saved\);\s*opened = true;/,
    "无 ?paper= 时要按上一段现场回来");
  assert.match(start, /if \(!opened && state\.papers\.length\) await selectPaper\(state\.papers\[0\]\.id\);/,
    "现场用不了才退回第一份");
  // 现场里那份卷可能已经归档 —— 归档卷不在 state.papers 里（loadPapers 只取
  // 未归档的）。拿 state.papers 当准入条件，归档卷的现场会被静悄悄丢掉，
  // 用户看到的是「又回到第一份卷」。实测就是这样：26e190fe 已归档，
  // remembered 恒为 false。
  assert.doesNotMatch(start, /const remembered = [^;]*state\.papers\.some\([^;]*\);/,
    "不能拿 state.papers 的判断结果当准入条件");
  assert.match(start, /const data = await api\(`\/api\/papers\/\$\{saved\.paperId\}`\);/,
    "不在列表里就按 id 拉一次，和 ?paper= 的归档兜底同一条路");
  assert.match(start, /if \(!data\.paper\.archived\) state\.papers\.push\(data\.paper\);/);
  assert.match(start, /catch \(error\) \{[\s\S]*?if \(!opened && state\.papers\.length\)/,
    "卷被删了不能卡在空白页，要退回第一份并说清楚");
}

// 顶栏两个页面的「录入终审」都是不带参数的整页跳转，恢复只能靠 sessionStorage。
{
  const index = fs.readFileSync(require.resolve("./index.html"), "utf8");
  const library = fs.readFileSync(require.resolve("./library.html"), "utf8");
  assert.match(index, /<a class="active" href="\/" aria-current="page">录入终审<\/a>/);
  assert.match(library, /<a class="header-link" href="\/">录入终审<\/a>/);
  assert.match(helpers, /window\.sessionStorage/);
  assert.doesNotMatch(helpers, /window\.localStorage/,
    "用 sessionStorage：换一台机器、隔一天回来，不该被旧现场劫持");
}

console.log("Review restore: filter, current card and expanded cards survive a round trip through 正式题库; URL wins; missing or invisible targets degrade quietly: OK");
