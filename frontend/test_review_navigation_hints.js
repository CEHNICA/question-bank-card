"use strict";

const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const App = require("./app.js");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8").replace(/\r\n/g, "\n");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");

// Run the real N handler and its stepping function against rendered cards.
// N locates the next question that still needs checking, so the old "visit every
// card in order" expectation no longer holds: approved cards are stepped over.
const step = source.slice(source.indexOf("  function moveNextCard()"), source.indexOf("  // ---------------------------------------------------------------- 展开 / 收起"));
const keyboard = source.slice(source.indexOf('  document.addEventListener("keydown", (event) => {\n    if (event.defaultPrevented || event.ctrlKey'), source.indexOf("  // ---------------------------------------------------------------- 原卷截图"));
function navigation(ids = [11, 12, 13, 14, 15], passed = [], offscreen = []) {
  const nodes = new Map(), selected = [], expanded = [], messages = [], handlers = [];
  const cards = ids.map((id, index) => ({ dataset: { id: String(id) }, inView: true,
    rect: { top: 140 + index * 320, bottom: 460 + index * 320 }, getBoundingClientRect() { return this.rect; } }));
  const state = { current: ids[0], followHold: false, filter: "all", questions: [...ids, ...offscreen].map((id, index) => ({ id, number: index + 1,
    state: index === 4 ? "yellow" : "green", approved: passed.includes(id) })) };
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, { open: false, hidden: false });
    return nodes.get(id);
  };
  const context = { state, $, QBUpload: App, document: { addEventListener: (name, fn) => handlers.push(fn) },
    cardNodes: () => cards, viewTop: () => 100, onScreen: card => card.inView,
    questionById: id => state.questions.find(q => q.id === id), anyDialogOpen: () => Boolean(context.dialogOpen),
    // The app's own rule, reduced to what this fixture knows about a question.
    needsReview: q => Boolean(q) && !q.approved && ["green", "yellow", "red"].includes(q.state),
    FILTERS: [{ key: "all", label: "全部" }, { key: "todo", label: "需要核查" }, { key: "approved", label: "已通过" }],
    autoExpandOnMove: id => expanded.push(id), toast: text => messages.push(text),
    setCurrent: (id, options) => { assert.equal(options.scroll, true); state.current = id; state.followHold = true; selected.push(id); },
    viewerKey: event => context.viewerEvents.push(event.key), viewerEvents: [] };
  vm.runInNewContext(step + keyboard, context);
  function press(extra = {}) {
    let prevented = false;
    const event = { key: "n", target: { closest: () => null }, preventDefault() { prevented = true; }, ...extra };
    handlers[0](event);
    return prevented;
  }
  return { context, state, cards, selected, expanded, messages, press, $ };
}
const next = navigation();
for (const id of [12, 13, 14, 15]) { assert(next.press()); assert.equal(next.state.current, id); }
assert.deepEqual(next.selected, [12, 13, 14, 15], "Each press visits the next question that still needs checking");
next.press(); assert.equal(next.state.current, 11, "Past the last question N comes back around to an earlier one still needing a check");
const done = navigation([11, 12, 13], [11, 12, 13]);
done.press(); assert.equal(done.state.current, 11, "A paper with nothing left to check stays put");
assert.match(done.messages.at(-1), /没有需要核查的题/);
assert.equal(done.selected.length, 0);
const skipping = navigation([11, 12, 13, 14, 15], [12, 13]);
skipping.press(); assert.equal(skipping.state.current, 14, "Approved questions in the middle are stepped over");
assert.deepEqual(skipping.selected, [14]);
skipping.press(); assert.equal(skipping.state.current, 15);
const wrapped = navigation([11, 12, 13], [12, 13]);
wrapped.state.current = 13; wrapped.press();
assert.equal(wrapped.state.current, 11, "Past the last question N comes back to one still needing a check");
const hidden = navigation([11, 12, 13], [11, 12, 13], [14]);
hidden.state.filter = "approved"; hidden.press();
assert.equal(hidden.selected.length, 0);
assert.match(hidden.messages.at(-1), /「已通过」这一栏里没有需要核查的题，另外 1 道在别的栏目里/,
  "Hiding the remaining questions behind a filter is named, not reported as a finished paper");
const filtered = navigation([11, 13, 15]); filtered.press(); assert.equal(filtered.state.current, 13); filtered.press(); assert.equal(filtered.state.current, 15);
const initial = navigation(); initial.state.current = null; initial.press(); assert.equal(initial.state.current, 12, "The first visible question is the initial origin");
const scrolled = navigation(); scrolled.cards[0].inView = false; scrolled.cards[0].rect.bottom = 80;
scrolled.press(); assert.equal(scrolled.state.current, 12, "A selected card stays the origin even when focus or scrolling has put it outside the viewport");
const unselectedScroll = navigation(); unselectedScroll.state.current = null; unselectedScroll.cards[0].inView = false; unselectedScroll.cards[0].rect.bottom = 80;
unselectedScroll.press(); assert.equal(unselectedScroll.state.current, 13, "Only an unselected list uses the top visible question as origin");
const rapid = navigation(); rapid.press(); rapid.cards[1].inView = false; rapid.press(); assert.equal(rapid.state.current, 13, "A queued smooth scroll cannot reset the next press to the old viewport");
const guarded = navigation();
for (const extra of [{ repeat: true }, { isComposing: true }, { keyCode: 229 }, { defaultPrevented: true }, { ctrlKey: true }, { altKey: true }, { metaKey: true },
  { target: { closest: selector => selector.includes("input") ? {} : null } },
  { target: { closest: selector => selector.includes("contenteditable") ? {} : null } }]) {
  guarded.press(extra); assert.equal(guarded.selected.length, 0, "Held keys, text input, IME and modified shortcuts do not step");
}
guarded.context.dialogOpen = true; guarded.press(); assert.equal(guarded.selected.length, 0, "An open settings, crop or confirmation dialog blocks N");
guarded.context.dialogOpen = false; guarded.$("paperView").hidden = true; guarded.press(); assert.equal(guarded.selected.length, 0);
guarded.$("paperView").hidden = false; guarded.press({ key: "N" }); assert.equal(guarded.state.current, 12);

// The review bubble and persistent in-page shortcut strip are removed. Full
// shortcut help remains reachable from Settings and the keyboard shortcut.
assert.doesNotMatch(html, /reviewGuidanceHint|reviewGuidanceDismiss|class="key-hints"/);
assert.doesNotMatch(source, /createReviewGuidance|renderReviewGuidance|qb-review-guidance-/);
assert.match(html, /id="settingsRestoreHints"[^>]*>恢复(?:已关闭的)?操作提示/);
const hintRestore = source.slice(source.indexOf('$("settingsRestoreHints").addEventListener'), source.indexOf('window.addEventListener("qb:hints-restored"'));
assert.match(hintRestore, /setCropGuidanceEnabled\(true\)/, "Help restores the existing permanent crop guidance opt-out too");
assert.doesNotMatch(hintRestore, /paperError|cutReadingError|pageCropResult|editGuard|\/api\//, "Guidance changes cannot hide failures, unsaved warnings or submit work");
const historical = "本地文字层没有可靠题卡，按本次授权尝试已配置的 MinerU。";
assert.equal(App.historicalParseReason({ status: "ready", processing_plan: { fallback_reason: historical } }), true);
for (const status of ["queued", "parsing", "failed", "needs_grouping"]) {
  assert.equal(App.historicalParseReason({ status, processing_plan: { fallback_reason: historical } }), false);
}
for (const reason of ["MinerU 服务暂时不可用，原页保留。", "有一页文字乱码。", "未切出 3 页题目，请补齐。", "题号重叠，请确认。", "未知的新警告"]) {
  assert.equal(App.historicalParseReason({ status: "ready", processing_plan: { fallback_reason: reason } }), false, "Only known completed route history leaves the main status");
}

// The same single-entry rule covers empty and populated manual/native stages.
const blank = App.cutReadingSummary([]), image = App.cutReadingSummary([{ id: 1, body_mode: "source_image", regions: [{ bbox: [0, 0, 10, 10] }] }]);
for (const parse_mode of ["manual", "native"]) {
  assert.equal(App.showCutReadingStage({ parse_mode, status: "ready" }, blank), true);
  assert.equal(App.showCutReadingStage({ parse_mode, status: "ready" }, image), true);
  assert.equal(App.showCutReadingStage({ parse_mode, status: "failed" }, blank), true);
}
for (const status of ["queued", "parsing", "segmenting", "needs_grouping"]) {
  assert.equal(App.showCutReadingStage({ parse_mode: "mineru", status }, image), false, "An active cloud cut has its direct stop-and-manual action rather than another crop stage");
}
assert.equal(App.showCutReadingStage({ parse_mode: "mineru", status: "ready" }, blank), false);
assert.equal(App.showCutReadingStage({ parse_mode: "mineru", status: "ready" }, image), true);
assert.equal(App.showCutReadingStage({ parse_mode: "manual", status: "ready", demo: true }, blank), false);
const mainStrip = html.slice(html.indexOf('</div>', html.indexOf('id="paperMenu"')), html.indexOf('id="cutReadingStage"'));
assert.doesNotMatch(mainStrip, /paperContinueAi|settingsManualFallback/,
  "1.12.6: no cut-mode switch is duplicated outside 试卷操作");
const menu = html.slice(html.indexOf('id="paperMenu"'), html.indexOf('id="paperStatus"'));
assert.match(menu, /id="paperContinueAi"/);
assert.match(menu, /id="settingsManualFallback"/);
assert.equal((html.match(/id="paperContinueAi"/g) || []).length, 1, "The existing guarded AI continuation has one menu entry");
// 1.12.5：approve-green 只有一个「试卷操作」入口，
// 而且它必须把过不去的题逐条说出来——不许出现第二个"全部通过"的按钮。
const approveEntries = (html.match(/approve-green/g) || []).length;
assert.equal(approveEntries, 0, "approve-green 只经脚本调用，不出现在静态标记里");
assert.equal((html.match(/id="approveAllGreen"/g) || []).length, 1, "一键通过只有一个入口");
assert.doesNotMatch(html, /id="approveGreen"/);
assert.match(source, /\$\("approveAllGreen"\)\.addEventListener\("click", approveAllGreen\)/);
const tourSteps = source.slice(source.indexOf("  const TOUR_STEPS ="), source.indexOf("  const tour ="));
assert.equal((tourSteps.match(/querySelector\("\.card-tick"\)/g) || []).length, 1, "Automatic banking shares the single approval lesson");
assert.match(tourSteps, /自动入库/);
assert.match(tourSteps, /通过即入库/);
assert.doesNotMatch(tourSteps, /publishButton/, "The tour never points to an obsolete second bank action");

// Render real stage actions and the real empty-state branch together. Neither
// depends on the previous stage's hidden flag, so an initial/polled render cannot
// accidentally produce two primary crop buttons.
const { node, el } = require("./credential-test-dom.js");
const stageCode = source.slice(source.indexOf("  function renderCutReadingStage()"), source.indexOf("  function openManualCut()"));
const emptyStart = source.indexOf("    if (!shown.length) {");
const emptyCode = source.slice(emptyStart, source.indexOf("    R.fitOptions(container);", emptyStart));
function cutting(paper, questions = []) {
  const nodes = new Map(), container = node("cards");
  const $ = id => { if (!nodes.has(id)) nodes.set(id, { ...node(id), dataset: {} }); return nodes.get(id); };
  // Deliberately stale DOM visibility: the new shared decision must win.
  $("cutReadingStage").hidden = false;
  const context = { $, state: { paper, paperId: "p", questions, filter: "all" }, QBCutReading: App, QBProgress: App,
    ACTIVE_STATUS: new Set(["queued", "parsing", "segmenting", "reading"]), container, shown: [], el,
    directImageReview: new Set(), cutReadingErrors: new Map(), cutReadingStopErrors: new Map(), cutReadingRequests: new Set(), cutReadingStops: new Set(),
    manualSwitches: new Set(), aiCutContinuations: new Set(), paperReadSubmissionPending: () => false,
    openManualCut() {}, focusCutReview() {}, readCutQuestions() {}, stopCutReading() {}, document: { createTextNode: text => text },
    button(label, className, callback) { const result = node("", "button", label); result.className = className; result.callback = callback; return result; } };
  vm.runInNewContext(stageCode, context);
  context.renderCutReadingStage();
  vm.runInNewContext(emptyCode, context);
  const stageButtons = $("cutReadingStage").hidden ? [] : $("cutReadingActions").children.filter(item => /切题与校正/.test(item.textContent));
  const emptyButtons = container.children.flatMap(item => item.children).filter(item => item.id === "emptyManualCut");
  return { $, context, stageButtons, emptyButtons };
}
for (const parse_mode of ["manual", "native"]) {
  const ready = cutting({ parse_mode, status: "ready", pages: [{ page_idx: 0 }] });
  assert.equal(ready.stageButtons.length, 1); assert.equal(ready.emptyButtons.length, 0);
  const populated = cutting({ parse_mode, status: "ready", pages: [{ page_idx: 0 }] }, [{ id: 1, approved: true, body_mode: "source_image", regions: [{ bbox: [0, 0, 10, 10] }] }]);
  assert.equal(populated.stageButtons.length, 1); assert.equal(populated.emptyButtons.length, 0);
}
const ordinaryCloud = cutting({ parse_mode: "mineru", status: "ready", pages: [{ page_idx: 0 }] });
assert.equal(ordinaryCloud.stageButtons.length, 0); assert.equal(ordinaryCloud.emptyButtons.length, 1, "Cloud-ready empty papers keep one usable manual fallback");
for (const status of ["queued", "parsing", "segmenting"]) {
  const pending = cutting({ parse_mode: "mineru", status, pages: [{ page_idx: 0 }] });
  assert.equal(pending.stageButtons.length + pending.emptyButtons.length, 0, "The existing direct stop-and-manual recovery owns cloud wait states");
}

console.log("Review N: the next question that still needs checking, skipping approved ones, wrapping at the end, naming a filter that hides the rest, first-card origin, scroll fencing, held-key and IME guards; review prompts removed, fault visibility, single cutting entry and automatic-approval tutorial: OK");
