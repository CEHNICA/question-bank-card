"use strict";

// Tag proposals are explicit, live in the formal library, and never join the
// answer-generation intake path or the old flat text catalogue.
const assert = require("node:assert/strict");
const fs = require("node:fs");

const panelJs = fs.readFileSync(require.resolve("./library-ai-settings.js"), "utf8");
const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryHtml = fs.readFileSync(require.resolve("./library.html"), "utf8");

assert.match(panelJs, /只在正式题库中由你主动发起/,
  "settings explain that AI tag proposals only happen after a user action");
assert.doesNotMatch(panelJs, /新题入库时生成标签|libraryAITagsIntake/,
  "settings contain no tag-on-intake control");
assert.match(panelJs, /on_intake: \{ tags: false, answer:/,
  "saving settings always disables legacy automatic tag generation");
assert.doesNotMatch(panelJs, /api\/settings\/knowledge|libraryAIKnowledge|openCatalogue/,
  "settings do not read or present the legacy text catalogue");

assert.match(libraryHtml, /id="libraryTagManager"/,
  "the formal library owns tag directory management through its dedicated page entry");
assert.match(libraryJs, /AI 建议标签/,
  "the library exposes an explicit per-question suggestion action");
assert.match(libraryJs, /只处理当前这批/,
  "the library can explicitly queue suggestions for the full current result set");
// 三个批量按钮以前叫「全库缺少知识点」「当前筛选全部」，看不出差别，
// 只能靠 784 与 781 相差 3 去猜。名字改成人话，并且每个都必须在悬停时说清
// 「处理哪些题」和「会消耗额度」，否则下次还会有人点错。
assert.match(libraryJs, /补齐缺的知识点标签/,
  "the whole-library batch action says what it fills in");
assert.match(libraryJs, /补齐缺的参考答案/,
  "the answer batch action reads as the same kind of sentence");
assert.match(libraryJs, /tags\.title = !apiReady[\s\S]*?不受上面的筛选影响[\s\S]*?消耗额度/,
  "the whole-library batch action explains its scope and its cost on hover");
assert.match(libraryJs, /current\.title = apiReady[\s\S]*?只处理你现在筛选出来的这[\s\S]*?消耗额度/,
  "the current-result batch action explains its scope and its cost on hover");
assert.match(libraryJs, /answers\.title = !apiReady[\s\S]*?消耗额度/,
  "the answer batch action explains its cost on hover");
assert.doesNotMatch(libraryJs, /全库缺少知识点|当前筛选全部|原卷无答案题/,
  "the internal wording is gone from the batch buttons");
// 按钮上写的题数必须等于「点下去真会排上」的那一批。已经有建议在等或在跑的题
// 也算没打标签，却再排一次什么也排不上 —— 按钮写着 805、点完没反应，比不给
// 反馈更让人以为坏了。
assert.match(libraryJs, /补齐缺的知识点标签 · \$\{readyCount\} 题/,
  "the whole-library batch button counts the questions it would really queue");
assert.match(libraryJs, /const readyCount = Number\(state\.tagWork\?\.untagged_ready\) \|\| 0/,
  "the batch button reads the queueable count, not the wider untagged count");
assert.match(libraryJs, /未标知识点 · \$\{missingCount\} 题/,
  "the untagged filter keeps the wider count, because that is what it filters to");
// 整库排队会逐题登记，题多时要忙十几秒。原来这段时间界面一动不动，
// 用户以为卡死。现在必须：按钮先自己变样，再发请求；并且面板上留一行
// 「正在处理多少道」，让排完队之后也不用一直盯着。
assert.match(libraryJs, /label\.nodeValue = "正在排队…"/,
  "the shared state applier writes the queueing wording onto the button text node");
assert.match(libraryJs, /if \(queueBusy && control\.dataset\.queueAction === queueAction\) markQueueing\(control\);\s*else unmarkQueueing\(control\);/,
  "the queueing wording follows the shared state and only ever lands on the pressed button");
// 认「刚才点的是哪一个」靠的是渲染时就写死的标记，不是节点也不是名字（名字里
// 带着题数，重画后会变；节点重画一次就被换掉了）。
assert.match(libraryJs, /tags\.dataset\.queueAction = "tags";/,
  "each batch button carries a stable identity that survives a re-render");
assert.match(libraryJs, /current\.dataset\.queueAction = "tags-current";/,
  "the current-filter batch button is distinguishable from the whole-library one");
assert.match(libraryJs, /answers\.dataset\.queueAction = "answer";/,
  "the answer batch button carries its own identity too");
assert.match(libraryJs, /queueBusy = true;\s*queueAction = action \|\| "";\s*applyBatchButtonState\(\)/,
  "starting a queue records which button was pressed and immediately runs the one shared rule");
assert.match(libraryJs, /control\.disabled = queueBusy \|\| selectedView \|\| control\.dataset\.disabledInSelectedView === "true"/,
  "locking and unlocking share one rule, so an API-less button is not re-enabled by mistake");
assert.match(libraryJs, /已等 \$\{spent\} 秒/,
  "the queueing line counts the seconds so a slow press still looks alive");
assert.match(libraryJs, /AI 正在处理：/,
  "the batch panel keeps saying how many questions are still being processed");
// 重画之后，面板上那一行也必须接着说「正在登记…」。它每次重画都是新节点，
// 内容只能按当下的状态算，不能永远只写「AI 还在跑多少道」那一套。
assert.match(libraryJs, /stateLine\.textContent = queueBusy \? queueingText\(queueCount, 0\) : workingSummary\(queuedTags, queuedAnswers\);/,
  "a rebuilt panel line still reports the queueing in progress");
assert.match(libraryJs, /confirmBulkTags\(readyCount, missingCount\)/,
  "the whole-library tag button asks before spending quota");
assert.match(libraryJs, /confirmBulkAnswers\(answerCount\)/,
  "the whole-library answer button asks before spending quota");
assert.match(libraryJs, /AI 建议待核对/,
  "generated candidates are labeled as pending human review");
assert.match(libraryJs, /pending.*checkbox|checkbox.*pending/s,
  "candidate acceptance is a separate user decision");
assert.match(libraryJs, /error\.stale = body\.stale === true/,
  "the client recognizes proposals made stale by a catalogue change");
assert.match(libraryJs, /if \(error\.stale\) \{\s*await refreshTaggedPublications\(\[item\.id\]\)/,
  "a stale acceptance response updates only the affected card and keeps the reading position");
assert.match(libraryJs, /tag-suggestion-accept[\s\S]*?tag-suggestion-reject[\s\S]*?"accept", panel, \[index\][\s\S]*?"reject", panel, \[index\]/,
  "each original suggestion has direct acceptance and rejection without implicit batch selection");
assert.match(libraryJs, /sessionStorage\.getItem\(suggestionDraftKey\)/,
  "unsubmitted AI-review drafts can be restored after a same-tab reload");
assert.match(libraryJs, /sessionStorage\.setItem\(suggestionDraftKey/,
  "edits are saved into the current browser session");
assert.match(libraryJs, /suggestionLocks\.has\(suggestion\.id\)[\s\S]*?suggestionLocks\.add\(suggestion\.id\)/,
  "duplicate per-entry and batch submissions share a single lock");
assert.match(libraryJs, /const attempt = previous \|\| \{[^\r\n]*signature, payload: \{[\s\S]*?request_id: tagRequestId\(\)/,
  "an uncertain submission reuses its payload and request identity");
assert.match(libraryJs, /JSON\.stringify\(attempt\.payload\)/,
  "normal and retry submissions send the saved attempt, rather than creating a new request");
assert.match(libraryJs, /sessionStorage\.setItem\(suggestionDraftKey \+ ":requests", JSON\.stringify\(Object\.fromEntries\(suggestionAttempts\)\)\)/,
  "uncertain AI request identities survive a same-tab page reload alongside the draft");
assert.match(libraryJs, /tag-suggestion-retry", "核实上次提交"/,
  "an uncertain AI submission provides a visible recovery action");
assert.match(libraryJs, /retryOriginal && previous \? \{ action: previous\.payload\.action/,
  "recovery verifies the original decision rather than adopting later draft changes");
assert.match(libraryJs, /\["failed", "no_match", "review", "accepted", "rejected", "stale"\]\.includes\(item\.tag_suggestion\.status\)/,
  "failed, undecidable and no-match suggestions offer an explicit retry");

console.log("Library tag AI settings and explicit candidate workflow: OK");
