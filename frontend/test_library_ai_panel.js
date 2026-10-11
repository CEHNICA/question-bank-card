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
assert.match(libraryJs, /当前筛选全部/,
  "the library can explicitly queue suggestions for the full current result set");
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
