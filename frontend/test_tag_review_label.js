// 1.26.46：「待核对」这一栏里其实有两种东西 —— AI 建议，和从旧标签留底里接回来的
// 旧标签。数目不一样，处理方式也不一样（旧标签是「确认」，AI 建议是「采纳」）。
// 界面原来只有一句「AI 建议待核对」，旧标签一批接回来之后就成了假话
// （那 189 张卡的 AI 建议其实一条都不剩）。
// 这里把两个小工具真跑一遍，看它们在各种情形下各说什么 —— 不能只查源码里有没有那句话。
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const root = path.join(__dirname, "..");
const js = fs.readFileSync(path.join(root, "frontend", "library.js"), "utf8");

const start = js.indexOf("  function tagReviewCounts() {");
const marker = "  function tagReviewLabel(counts) {";
const mid = js.indexOf(marker);
assert.ok(start >= 0 && mid > start, "the two helpers are where this test expects them");
const end = js.indexOf("\n  }", js.indexOf('return "AI 建议待核对";', mid));
assert.ok(end > mid, "tagReviewLabel ends where this test expects it");
const snippet = js.slice(start, end + 4);

function countsFor(tagWork) {
  const context = vm.createContext({ state: { tagWork } });
  vm.runInContext(
    `${snippet}\nthis.tagReviewCounts = tagReviewCounts; this.tagReviewLabel = tagReviewLabel;`,
    context
  );
  const counts = context.tagReviewCounts();
  return { counts, label: context.tagReviewLabel(counts) };
}

// 只有旧标签：不能叫「AI 建议待核对」—— 那批根本不是 AI 建议。
const legacyOnly = countsFor({ pending_review: 189, pending_suggestions: 0, pending_legacy: 189 });
assert.equal(legacyOnly.label, "旧标签待核对", "old tags are not AI suggestions");
assert.equal(legacyOnly.counts.legacy, 189, "the old tags are counted on their own");
assert.equal(legacyOnly.counts.suggestions, 0, "the AI suggestions are counted on their own");

// 两种都有：说「待核对」，两笔各自报出来。
const both = countsFor({ pending_review: 628, pending_suggestions: 439, pending_legacy: 189 });
assert.equal(both.label, "待核对", "with both kinds waiting the button just says 待核对");
assert.equal(both.counts.total, 628, "the total is the union of both kinds");
assert.equal(both.counts.legacy, 189, "the old tags are counted on their own");
assert.equal(both.counts.suggestions, 439, "the AI suggestions are counted on their own");

// 只有 AI 建议：老话照旧。
const suggestionOnly = countsFor({ pending_review: 439, pending_suggestions: 439, pending_legacy: 0 });
assert.equal(suggestionOnly.label, "AI 建议待核对", "AI suggestions keep their own name");

// 旧版服务端没有那两笔：不能把提示整条弄没，按老口径走。
const oldServer = countsFor({ pending_review: 439 });
assert.equal(oldServer.label, "AI 建议待核对", "an older server still gets the old wording");
assert.equal(oldServer.counts.suggestions, 439, "an older server counts its pending as suggestions");
assert.equal(oldServer.counts.legacy, 0, "an older server has no old tags to report");

// 一个数都没有：也不许说出「待核对」。
const none = countsFor({ pending_review: 0, pending_suggestions: 0, pending_legacy: 0 });
assert.equal(none.label, "AI 建议待核对", "with nothing waiting the label stays the default one");
assert.equal(none.counts.total, 0, "nothing waiting means nothing waiting");

// 悬停说明也要照实说：旧标签那一步是「确认」，不调用 AI、不消耗额度。
// 这两条是静态钉住 —— 真跑按钮要整套界面，代价太大；但少了这两句，
// 老师就会以为点一下要花钱。
assert.match(js, /: legacyCount\s*\n\s*\? `这 \$\{legacyCount\} 道题的旧标签等你确认/,
  "the hover says the old tags are waiting to be confirmed");
assert.match(js, /这一步不调用 AI，也不消耗额度/,
  "the hover says confirming costs nothing");

// 标签管理页那句话也不能把旧标签说成 AI 建议。
const managerJs = fs.readFileSync(path.join(root, "frontend", "library-tag-manager.js"), "utf8");
assert.match(managerJs, /另有 \$\{legacy\} 道题的旧标签等你确认/,
  "the tag directory page names the old tags for what they are");
assert.match(managerJs, /回题库核对这 \$\{legacy\} 道题的旧标签/,
  "the tag directory page button says what it acts on");
assert.doesNotMatch(managerJs, /\$\{pending\} 道题有 AI 标签建议等你核对/,
  "the tag directory page never calls the old tags AI suggestions");

console.log("tag review label checks: OK");
