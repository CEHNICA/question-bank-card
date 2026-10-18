"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("./library.js"), "utf8");
function section(start, end) {
  const first = source.indexOf("  " + start), last = source.indexOf("  " + end, first + 1);
  assert.ok(first >= 0 && last > first, `${start} remains available for behavior checks`);
  return source.slice(first, last);
}

const root = { id: "root", name: "函数", kind: "knowledge", active: true, parent_ids: [], child_ids: ["quadratic"] };
const second = { id: "second", name: "多项式函数", kind: "knowledge", active: true, parent_ids: [], child_ids: ["quadratic"] };
const quadratic = { id: "quadratic", name: "二次函数", kind: "knowledge", active: true, parent_ids: ["root", "second"], child_ids: ["minimum"] };
const minimum = { id: "minimum", name: "二次函数最值", kind: "knowledge", active: true, parent_ids: ["quadratic"], child_ids: [] };
const catalogue = { revision: 9, tags: [root, second, quadratic, minimum] };
const hierarchy = vm.createContext({ tagCatalogue: catalogue });
vm.runInContext(section("function hierarchyTag(", "function cancelTagHoverClose("), hierarchy);
assert.deepEqual(Array.from(hierarchy.tagHierarchyRows("quadratic", "up"), value => value.id).sort(), ["root", "second"]);
assert.deepEqual(Array.from(hierarchy.tagHierarchyRows("quadratic", "down"), value => value.id), ["minimum"]);
assert.deepEqual(Array.from(hierarchy.tagAncestorPaths("minimum").paths, path => Array.from(path, value => value.id)).sort(),
  [["root", "quadratic", "minimum"], ["second", "quadratic", "minimum"]]);
assert.equal(hierarchy.tagAncestorPaths("minimum", 1).hasMore, true, "many-parent paths have an explicit continuation cue");
const deepTags = Array.from({ length: 120 }, (_, index) => ({ id: `d${index}`, name: `深度${index}`, parent_ids: index ? [`d${index - 1}`] : [], child_ids: [] }));
hierarchy.tagCatalogue = { tags: deepTags };
assert.equal(hierarchy.tagAncestorPaths("d119").paths[0].length, 120, "valid deep paths are not silently truncated");

const offscreenInspector = { id: "q-first", getBoundingClientRect: () => ({ top: -2332, bottom: -2000 }) };
const visibleSource = { id: "q-source", getBoundingClientRect: () => ({ top: 580, bottom: 850 }) };
const scroller = { scrollTop: 2332, scrollLeft: 0 };
// 1.25.37：inView 从 selectInspectorQuestion 内部提到了模块层（syncLocateButton
// 在别的函数里要用它，够不到局部常量），所以这段单跑时它不在切片里了。
// 注意它在文件里的位置（1351 行）比 captureLibraryViewport（1275 行）**靠后**，
// 所以只能先切它、单独跑一次，不能按源码顺序拼在前面 —— const 靠后定义会拿到 undefined。
const inViewCtx = vm.createContext({ window: { innerHeight: 900 } });
vm.runInContext(section("const inView = element =>", "function renderInspector("), inViewCtx);
const viewport = Object.assign(inViewCtx, vm.createContext({ state: { inspectorId: "first" }, tagHover: { publicationId: "source" },
  window: { innerHeight: 900 },
  document: { scrollingElement: scroller, getElementById: id => id === "q-first" ? offscreenInspector : id === "q-source" ? visibleSource : null },
  ui: { list: { querySelectorAll: () => [offscreenInspector, visibleSource] } }, requestAnimationFrame: callback => callback() }));
vm.runInContext(section("function captureLibraryViewport(", "function inspectorFact("), viewport);
const position = viewport.captureLibraryViewport(); assert.equal(position.anchorId, "q-source", "an offscreen inspector cannot anchor a correction on another visible card");
viewport.restoreLibraryViewport(position); assert.equal(scroller.scrollTop, 2332, "restoring a correction never scrolls back to the offscreen inspector");

(async () => {
  const suggestion = { status: "accepted", suggestions: [{ tag_id: "quadratic", decision: { action: "accept" } }] };
  const item = { id: "source-publication", tag_revision: 7, tag_suggestion: suggestion,
    tag_assignments: [{ tag: quadratic, source: "ai_accepted", scope: { whole: true }, rationale: "已核对依据" }, { tag: root, source: "human" }] };
  const posts = [], notices = [];
  const mutation = vm.createContext({ state: { catalog: new Map([[item.id, item]]), inspectorId: "another-publication" },
    inlineTagLocks: new Set(), inlineTagStates: new Map(), tagHover: { publicationId: item.id }, tagCatalogue: catalogue,
    tagRequestId: () => "fixed-request", toast: message => notices.push(message),
    submitInlineTagAttempt: async (target, attempt) => { posts.push({ target, attempt }); return true; } });
  vm.runInContext(section("async function replaceInlineTag(", "document.addEventListener(\"pointerdown\""), mutation);
  await mutation.replaceInlineTag(item.id, "quadratic", quadratic);
  assert.equal(posts.length, 0, "same-tag selection never writes or increments a revision");
  await mutation.replaceInlineTag(item.id, "quadratic", minimum);
  assert.equal(posts.length, 1);
  const payload = JSON.parse(JSON.stringify(posts[0].attempt.payload));
  assert.deepEqual(payload, { publication_ids: [item.id], replace: { from_tag_id: "quadratic", to_tag_id: "minimum" },
    expected_revisions: { [item.id]: 7 }, expected_catalogue_revision: 9, request_id: "fixed-request" });
  assert.equal(item.tag_assignments.length, 2, "the client does not preemptively strip other ancestor tags");
  await mutation.replaceInlineTag(item.id, "quadratic", { id: "method", kind: "method" });
  assert.equal(posts.length, 1, "related methods cannot replace a knowledge tag");
  mutation.inlineTagStates.set(item.id, { attempt: { payload } });
  await mutation.replaceInlineTag(item.id, "quadratic", root);
  assert.equal(posts.length, 1, "unresolved requests prevent a second edit of the same question");

  const results = vm.createContext({});
  vm.runInContext(section("function applyInlineTagResult(", "// Only the tag fields"), results);
  results.applyInlineTagResult(item, { publication_id: item.id, revisions: { [item.id]: 8 }, assignments: [{ tag: minimum, source: "human", scope: { whole: true } }] });
  assert.equal(item.tag_revision, 8); assert.equal(item.tag_assignments[0].tag.id, "minimum");
  assert.equal(item.tag_suggestion, suggestion, "refinement does not reset accepted AI history");

  const calls = [], locks = new Set(), states = new Map(), recoveryItem = { id: "recovery", tag_revision: 3 };
  const recovery = vm.createContext({ inlineTagLocks: locks, inlineTagStates: states, tagHover: null,
    patchTagPresentation: () => {}, renderTagHover: () => {}, applyInlineTagResult: () => {}, toast: () => {},
    refreshTaggedPublications: async () => {}, tagRequest: async (url, options) => {
      calls.push({ url, options });
      return { operations: [{ id: "saved-operation", request_id: "original-request", revisions: { recovery: 4 }, undone: false }] };
    } });
  vm.runInContext(section("async function submitInlineTagAttempt(", "async function removeInlineTag("), recovery);
  assert.equal(await recovery.submitInlineTagAttempt(recoveryItem, { uncertain: true, message: "已校正", payload: { request_id: "original-request", replace: { from_tag_id: "a", to_tag_id: "b" } } }), true);
  assert.equal(calls.length, 1, "a recovered committed result does not issue another POST");
  assert.match(calls[0].url, /operations\?request_id=original-request$/);
  assert.equal(states.get("recovery").operationId, "saved-operation"); assert.equal(locks.size, 0);
  console.log("Hierarchical hover paths, source-bound replacement, AI history and uncertain-result recovery: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
