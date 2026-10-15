"use strict";

// Integration checks plus execution of the actual query/scope helpers. Database
// mutations are exercised in core.test_library_tagging with isolated data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryHtml = fs.readFileSync(require.resolve("./library.html"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");
const managerJs = fs.readFileSync(require.resolve("./library-tag-manager.js"), "utf8");
const managerCss = fs.readFileSync(require.resolve("./library-tag-manager.css"), "utf8");
const urls = fs.readFileSync(require.resolve("../backend/qb_server/urls.py"), "utf8");
const service = fs.readFileSync(require.resolve("../backend/core/library_tagging.py"), "utf8");

assert.match(libraryJs, /node\("button", "library-tags-edit", "[＋+] 添加标签"\)/,
  "The selected question provides an explicit add-tag entry, including questions without tags");
const targetFunction = libraryJs.match(/function tagTarget\(item\) \{[^\r\n]*\}/)?.[0];
assert.ok(targetFunction, "the tag scope has one explicit entry point");
const context = vm.createContext({ state: { selected: new Set(["current-question", "another-question"]) } });
vm.runInContext(targetFunction, context);
assert.deepEqual(Array.from(context.tagTarget({ id: "current-question" })), ["current-question"],
  "editing the current selected question cannot silently edit another checked question");
assert.deepEqual(Array.from(context.tagTarget({ id: "unchecked-question" })), ["unchecked-question"],
  "a question's tag entry retains its scope even when other questions are checked");
assert.deepEqual(Array.from(context.tagTarget(null)), ["current-question", "another-question"],
  "the explicit batch entry operates on all checked question identities");
const queryStart = libraryJs.indexOf("  function libraryQuery("), queryEnd = libraryJs.indexOf("  function markLibraryResult(", queryStart);
assert.ok(queryStart >= 0 && queryEnd > queryStart, "the list query builder is available for behavior checks");
const queryState = { q: "  sin 函数  ", document: "paper-1", type: "single_choice", review: "human", answer: "no",
  tagAll: "knowledge-1,knowledge-2", tagAny: "method-1", tagNot: "custom-1", tagMode: "not",
  untaggedKnowledge: true, tagReview: true, sort: "source" };
const queryContext = vm.createContext({ URLSearchParams, state: queryState });
vm.runInContext(libraryJs.slice(queryStart, queryEnd), queryContext);
const expectedTagFields = { tag_all: "knowledge-1,knowledge-2", tag_any: "method-1", tag_not: "custom-1",
  tag_mode: "not", untagged_knowledge: "true", tag_review: "true" };
const currentQuery = queryContext.libraryQuery();
for (const [field, value] of Object.entries(expectedTagFields)) assert.equal(currentQuery.get(field), value,
  `the current UI state's ${field} must reach the server query`);
assert.equal(currentQuery.get("q"), "sin 函数"); assert.equal(currentQuery.get("review"), "human");
assert.equal(currentQuery.get("sort"), "source"); assert.equal(currentQuery.get("limit"), "40");
const savedQuery = queryContext.libraryQuery({ ...expectedTagFields, q: "  三角函数  ", sort: "recent" }, { limit: 80, offset: 40 });
for (const [field, value] of Object.entries(expectedTagFields)) assert.equal(savedQuery.get(field), value,
  `saved/filter API-shaped input preserves ${field} through the same builder`);
assert.equal(savedQuery.get("q"), "三角函数"); assert.equal(savedQuery.get("limit"), "80"); assert.equal(savedQuery.get("offset"), "40");
for (const mode of ["all", "any", "not"]) {
  assert.equal(queryContext.libraryQuery({ tagMode: mode }).get("tag_mode"), mode);
  assert.equal(queryContext.libraryQuery({ tag_mode: mode }).get("tag_mode"), mode);
}
const clearedQuery = queryContext.libraryQuery({ ...queryState, tag_all: "", tag_any: "", tag_not: "", tag_mode: "",
  untagged_knowledge: false, tag_review: false });
for (const field of Object.keys(expectedTagFields)) assert.equal(clearedQuery.has(field), false,
  `explicitly cleared ${field} cannot fall back to a stale UI alias`);
const browseStart = libraryJs.indexOf("  function browseTag("), browseEnd = libraryJs.indexOf("  function ensureTagManagerDialog(", browseStart);
assert.ok(browseStart >= 0 && browseEnd > browseStart);
let browseLoads = 0, browseUrl = null;
queryContext.syncUrl = () => { browseUrl = queryContext.libraryQuery(); };
queryContext.load = () => { browseLoads++; };
vm.runInContext(libraryJs.slice(browseStart, browseEnd), queryContext);
queryState.tag = "old-name-filter";
queryContext.browseTag({ id: "selected-tag" });
assert.equal(browseUrl.get("tag_any"), "selected-tag"); assert.equal(browseUrl.get("tag_mode"), "any");
for (const field of ["tag", "tag_all", "tag_not", "tag_review", "untagged_knowledge"]) assert.equal(browseUrl.has(field), false,
  `browsing a selected tag clears incompatible old ${field} constraints`);
assert.equal(browseUrl.get("review"), "human", "browsing a tag retains unrelated human-review filtering");
assert.equal(browseLoads, 1, "a selected-tag lookup performs one explicit list load");
assert.match(libraryHtml, /id="editSelectedTags"[^>]*disabled[^>]*>整理勾选题的标签/,
  "batch tagging has a separate control and cannot start with an empty selection");
assert.match(libraryJs, /tagTargetIds\.length > 500[\s\S]*?return;/,
  "oversized batches are rejected explicitly instead of silently truncated");
assert.match(libraryJs, /\/api\/library\/tags\/assignments\/batch/, "Picker writes through the versioned batch endpoint");
assert.match(libraryJs, /全部都有|部分有|全部没有/, "Multi-select makes the mixed state explicit");
assert.match(libraryJs, /撤销上次修改/, "Picker provides immediate undo");
assert.match(libraryJs, /tagPickerView === "favorite"/, "Favorites are available in the picker");
assert.match(libraryJs, /tagPickerView === "recent"/, "Recently used tags are available in the picker");
assert.match(managerJs, /导出旧标签映射/, "Legacy migration report remains available inside the catalogue manager");
assert.match(managerJs, /操作记录与撤销/, "Persistent tag-operation history is shown in the manager");
assert.match(managerJs, /tagManagerWorkspace/, "tag management has an independent page workspace");
assert.match(libraryJs, /window\.QBLibraryTagManager\.create\(/, "the question list integrates the independent manager");
assert.match(libraryHtml, /library-tag-manager\.js[\s\S]*?library\.js/, "the manager module loads before its consumer");
assert.match(managerCss, /\.tag-directory-body \{[^}]*grid-template-columns:/,
  "directory groups, results and selected-tag details have distinct layout regions");
assert.match(managerJs, /group_membership_batch[\s\S]*?tag_ids:/, "manager grouping preserves explicit selected tag identities");
assert.match(managerJs, /impact\?action=delete_tags[\s\S]*?impact_token: impact\.token/,
  "bulk deletion checks impact before submitting the confirmed mutation");
assert.match(managerJs, /impact\?action=merge_tags_batch[\s\S]*?impact_token: impact\.token/,
  "bulk merging checks full-library impact before submitting");
assert.match(libraryJs, /tag-suggestion-actions/, "AI suggestions require an explicit human action");
assert.match(libraryJs, /采纳所选建议/, "AI results become formal only through explicit acceptance");
assert.match(libraryJs, /当前筛选出来的 \$\{count\} 道题/, "Current-filter AI work confirms the full result count before queueing");
assert.match(libraryJs, /消耗额度/, "the confirmation states the quota cost before a bulk AI run is queued");
assert.match(libraryJs, /scope: "current_filter"/, "Current-filter AI tasks keep their explicit scope");
assert.match(libraryJs, /旧标签待核对/, "Unmapped legacy relationships remain visible for cleanup");
assert.match(libraryHtml, /包含全部[\s\S]*包含任一[\s\S]*排除这些/, "The library exposes all/any/exclusion tag matching");
assert.match(libraryCss, /\.tag-picker-row \{[\s\S]*var\(--tag-color/, "Tag colors are rendered in the selector");
assert.match(urls, /library_tagging\.legacy_tags_view/, "Legacy name-based clients use the guarded compatibility endpoint");
assert.match(urls, /library_tagging\.migration_report_view/, "The application exposes a migration report download");
assert.match(service, /"expected_revisions"/, "Human writes are revision-checked");
assert.match(service, /"suggestion:accept"/, "Suggestion acceptance is recorded as an undoable operation");
assert.match(service, /catalogue:.*action/, "Catalogue mutations are recorded for persistent undo");
assert.match(libraryJs, /系统模板/, "Users can explicitly browse and apply system taxonomy templates");
assert.match(managerJs, /清空标签目录/, "Users can explicitly reset the global catalogue from management");
const pickerSource = libraryJs.split("function ensureTagDialog()")[1]?.split("async function openTagEditor")[0];
assert.ok(pickerSource, "the question tag picker exists");
assert.doesNotMatch(pickerSource, /tagPickerCreateGroup|tagPickerTemplates|tagPickerReset|tagPickerManager/,
  "a question's picker excludes template and global destructive catalogue tools");
assert.match(libraryJs, /deleteCatalogueTag/, "Tag deletion is a first-class manager action");
assert.match(libraryJs, /AI 标签记录/, "Processed AI results remain available from per-question history");
assert.match(libraryJs, /if \(entry\.decision\) return/, "Resolved AI suggestions disappear from the review panel");
assert.match(service, /def impact_view/, "Global delete and reset expose read-only impact previews");
assert.match(service, /"delete_tag"/); assert.match(service, /"reset_empty"/);
assert.match(service, /"delete_tags"/); assert.match(service, /"merge_tags_batch"/);
assert.match(service, /impact_token/, "Deletion and reset use the guarded transactional service");
assert.match(service, /initialized/, "An intentionally empty catalogue stays initialized without reseeding");
assert.match(urls, /library_tagging\.templates_view/, "The system blueprint is queried read-only");
assert.match(urls, /library_tagging\.impact_view/, "The manager can inspect affected questions before deletion");
assert.doesNotMatch(service, /MAX_TAGS|最多\s*3\s*个知识点/, "Formal manual tagging has no three-tag business limit");

console.log("Formal tag picker, legacy compatibility, AI review, migration report and undo integration: OK");
