"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const queries = source.slice(source.indexOf("  function libraryQuery("), source.indexOf("  async function load("));
const navigation = source.slice(source.indexOf("  function questionViewerNavigation("), source.indexOf("  function openQuestionViewer("));
const visible = source.slice(source.indexOf("  function visibleItems("), source.indexOf("  function clearFilters("));
const first = Array.from({ length: 40 }, (_, index) => ({ id: `pub-${index + 1}`, number: index + 1, content: { stem: `完整题目 ${index + 1}` } }));
const second = Array.from({ length: 15 }, (_, index) => ({ id: `pub-${index + 41}`, number: index + 41, content: { stem: `完整题目 ${index + 41}` } }));
const state = { view: "all", items: first.slice(), total: 55, q: "  函数 $x$  ", document: "paper-original", type: "single_choice", review: "human", answer: "no", tag: "几何", sort: "source",
  basket: ["basket-unused"], basketMissing: [], selected: new Set(["pub-3"]), draft: { id: "immutable-draft", solutions: { "pub-2": "original-solution" } }, catalog: new Map([["unrelated-history", { id: "unrelated-history" }]]) };
const requests = []; let response = { items: second, total: 55 }, gate = null, failed = false;
const context = { state, URLSearchParams, fetch: async (url, options) => {
  assert(!options.method, "The viewer page loader is read-only"); requests.push({ url, options }); const body = response, fail = failed;
  if (gate) await gate; return { ok: !fail, json: async () => fail ? { error: "离线页读取失败" } : body };
} };
vm.createContext(context); vm.runInContext(queries + visible + navigation + "\nthis.make = questionViewerNavigation; this.mark = markLibraryResult; this.query = libraryQuery;", context);
const turn = async () => { for (let index = 0; index < 8; index++) await Promise.resolve(); };

(async () => {
  context.mark(context.query()); const generation = state.resultGeneration;
  const snapshot = JSON.stringify([state.items, state.basket, [...state.selected], state.draft, [...state.catalog]]);
  const nav = context.make(first[39]); assert.equal(nav.index, 39); assert.equal(nav.total, 55);
  assert.equal((await nav.load(27)).item.id, "pub-28"); assert.equal(requests.length, 0, "Moving among loaded results requires no list request");
  // Fields may hold a new, failed/pending search. Old cards must keep the
  // successful query which produced them, including all filters and order.
  state.q = "pending new search"; state.document = "pending paper"; state.type = "free_response"; state.sort = "recent";
  const fortieth = await nav.load(40, { signal: new AbortController().signal });
  assert.equal(fortieth.item.id, "pub-41"); assert.equal(fortieth.index, 40); assert.equal(fortieth.total, 55);
  const query = new URL(requests[0].url, "http://offline.local").searchParams;
  assert.equal(query.get("q"), "函数 $x$"); assert.equal(query.get("document"), "paper-original");
  assert.equal(query.get("type"), "single_choice"); assert.equal(query.get("review"), "human"); assert.equal(query.get("answer"), "no"); assert.equal(query.get("tag"), "几何");
  assert.equal(query.get("sort"), "source"); assert.equal(query.get("offset"), "40"); assert.equal(query.get("limit"), "40");
  assert.equal((await nav.load(54)).item.id, "pub-55"); assert.equal((await nav.load(38)).item.id, "pub-39");
  assert.equal(requests.length, 1, "A loaded viewer page is reused without re-reading or rebuilding the list");
  await assert.rejects(nav.load(55), /边界/);
  assert.equal(JSON.stringify([state.items, state.basket, [...state.selected], state.draft, [...state.catalog]]), snapshot);

  // Same-query job polling can change answers/tags and page sizes without
  // changing the navigation scope. Actual query/order changes invalidate it.
  state.items = state.items.map(item => ({ ...item, ai_answer: { answer: "updated draft" } }));
  const same = new URLSearchParams(state.resultQuery); same.set("limit", "100"); same.set("offset", "20"); context.mark(same);
  assert.equal(state.resultGeneration, generation); assert.equal((await nav.load(41)).item.id, "pub-42");
  context.mark(context.query()); assert.notEqual(state.resultGeneration, generation);
  await assert.rejects(nav.load(42), /已更新/);

  // A failure does not advance the local offset or lose the current question.
  context.mark(context.query()); const retry = context.make(state.items[39]);
  failed = true; await assert.rejects(retry.load(40), /离线页/); failed = false;
  assert.equal((await retry.load(40)).item.id, "pub-41");
  assert.equal(new URL(requests.at(-1).url, "http://offline.local").searchParams.get("offset"), "40");

  for (const invalid of [{ items: [first[39]], total: 55 }, { items: second, total: 54 }, { items: [], total: 55 }, { items: [second[0], second[0]], total: 55 }]) {
    response = invalid; const invalidNav = context.make(state.items[39]);
    await assert.rejects(invalidNav.load(40), /变化|不完整/);
  }
  response = { items: second, total: 55 };
  let release; gate = new Promise(resolve => { release = resolve; }); const delayed = context.make(state.items[39]);
  const pending = delayed.load(40); await turn();
  state.items = state.items.slice().reverse(); context.mark(new URLSearchParams(state.resultQuery));
  gate = null; release(); await assert.rejects(pending, /已更新/);

  // Even a transport that ignores abort cannot commit a late page into a pool.
  const controller = new AbortController(), cancelled = context.make(state.items[39]);
  let releaseAbort; gate = new Promise(resolve => { releaseAbort = resolve; }); const aborted = cancelled.load(40, { signal: controller.signal });
  await turn(); controller.abort(); gate = null; releaseAbort(); await assert.rejects(aborted, /停止/);
  const beforeRetry = requests.length; await cancelled.load(40); assert.equal(requests.length, beforeRetry + 1);

  state.view = "selected"; state.basket = ["pub-45", "missing", "pub-2"];
  state.catalog.set("pub-45", second[4]); state.catalog.set("pub-2", first[1]); state.basketMissing = [{ id: "missing" }];
  context.mark(); const selected = context.make(second[4]); assert.equal(selected.index, 0); assert.equal(selected.total, 2); assert(selected.note.includes("1 题暂不可查看"));
  const selectedRequests = requests.length; assert.equal((await selected.load(1)).item.id, "pub-2"); assert.equal(requests.length, selectedRequests);
  assert.equal(context.make({ id: "unrelated-history" }), null, "A cache entry outside this result/basket is never a navigation candidate");
  await assert.rejects(selected.load(2), /边界/); state.basket.push("newly-selected"); await assert.rejects(selected.load(1), /已更新/);
  assert(source.includes('card?.querySelector(".library-full-button")'), "Returning to the old complete-question dialog resolves the visible full-screen button");
  console.log("Full-screen result navigation: all filters/order, lazy page40 boundary, immutable selection/draft, quiet-refresh identity, retry/abort/fence and selected-only order: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
