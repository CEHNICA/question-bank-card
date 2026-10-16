"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

const controls = Object.fromEntries([
  "printDocument", "printAnswerLayout", "printPagination", "printOptionLayout", "printFontSize", "printAnswerSpace", "printStudentInfo", "printButton", "exportPdf", "exportWord", "exportSplit", "managePrintAnswers", "printIndividualQuestion", "printIndividualOption", "printIndividualSpace", "printIndividualBreak", "printIndividualHint", "printFigureBox", "printFigureList", "printFigureAlign"
].map(id => [id, { value: "", checked: false, disabled: false }]));
// 配图调整那两个控件要真的建出子节点，所以给一个刚好够用的假元素：只支持
// replaceChildren / 按 class 找子节点。真的浏览器里还有一整套，这里只管证明
// 「这道题有配图时，滑块和毫米数真的被建出来了」，格子长什么样归 CSS。
function stubElement() {
  return {
    value: "", checked: false, disabled: false, hidden: false, className: "",
    dataset: {}, style: {}, title: "", type: "", children: [], parentElement: null,
    setAttribute() {}, append(...kids) { kids.forEach(kid => { kid.parentNode = this; this.children.push(kid); }); },
    replaceChildren(...kids) { this.children = []; kids.forEach(kid => { kid.parentNode = this; this.children.push(kid); }); },
    // 认得 ".class" 和 ".parent .child" 两种写法。真实代码找的是 ".qb-figure img"
    // 这种后代选择器，只认单个 class 的话，假元素会给出一个假的「找不到」。
    querySelectorAll(selector) {
      const chain = selector.trim().split(/\s+/);
      const hit = (node, token) => token.startsWith(".")
        ? String(node.className).split(/\s+/).includes(token.slice(1))
        : String(node.tagName || "").toLowerCase() === token.toLowerCase();
      const matches = (node, position) => hit(node, chain[position])
        && (position === 0 || (node.parentNode && matches(node.parentNode, position - 1)));
      const found = [];
      const walk = node => node.children.forEach(child => {
        if (matches(child, chain.length - 1)) found.push(child);
        walk(child);
      });
      walk(this);
      return found;
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  };
}
controls.printFigureList = stubElement();
const stubDocument = { createElement: tag => { const element = stubElement(); element.tagName = tag; return element; } };
const ui = { printAnswers: {}, printOrigin: {}, printAi: {}, paper: { querySelectorAll: () => [], querySelector: () => null } };
const state = { features: { ai_answer: false } };
const printState = { items: [{}], missing: [], loading: false, exporting: false, availableAnswers: 0, tooWide: 0, autoOrigin: new Set() };
const sandbox = { $: id => controls[id], ui, state, printState, solutions: require("./library-solutions.js"), printAnswersPreference: true, document: { fonts: { ready: Promise.resolve() } }, setTimeout, clearTimeout,
  CSS: { escape: value => value }, node: (tag, className = "", text) => { const element = stubElement(); element.tagName = tag; if (className) element.className = className; if (text !== undefined && text !== null) element.textContent = String(text); return element; } };
vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf("  function normalizePrintOptions("), source.indexOf("  function printAnswerContent(")), sandbox);
const plain = value => JSON.parse(JSON.stringify(value));

// Existing drafts retain answer intent, origin and explicitly chosen AI reference.
sandbox.applyPrintOptions({ answers: false, origin: true, ai_answers: true });
assert.equal(controls.printDocument.value, "questions");
assert.equal(ui.printOrigin.checked, true);
assert.equal(ui.printAi.checked, false, "A disabled AI feature must not silently enter an output");
assert.equal(controls.printFontSize.value, "12");
assert.equal(controls.printStudentInfo.checked, true);
sandbox.applyPrintOptions({ answers: true });
assert.equal(controls.printDocument.value, "combined");

state.features.ai_answer = true;
sandbox.applyPrintOptions({ document: "answers", answers: false, font_size: 16, answer_space: "large", student_info: false, ai_answers: true });
assert.deepEqual(plain(sandbox.currentPrintOptions()), { answers: true, origin: false, ai_answers: true, answer_layout: "appendix", document: "answers", font_size: 16, answer_space: "large", answer_space_overrides: {}, student_info: false, pagination: "compact", option_layout: "auto", option_overrides: {}, figure_overrides: {}, question_breaks: [] });
assert.equal(controls.printAnswerLayout.value, "appendix", "A draft saved before answer positions existed keeps its original appendix layout");
sandbox.applyPrintOptions({ document: "combined", answer_layout: "inline", answer_space: "large" });
assert.equal(sandbox.currentPrintOptions().answer_layout, "inline");
assert.equal(sandbox.currentPrintOptions().answer_space, "none", "An inline teacher paper never inserts student writing space");
assert.equal(sandbox.normalizePrintOptions({ font_size: 6, answer_space: "custom", document: "invalid" }).font_size, 12);
assert.equal(controls.printPagination.value, "compact", "Old drafts use the paper-saving default");
sandbox.applyPrintOptions({ pagination: "keep", option_layout: "vertical", option_overrides: { a: "vertical", b: "auto", bad: "invalid" }, answer_space: "small", answer_space_overrides: { a: "none", b: "large", bad: "invalid" }, figure_overrides: { a: { width: { 0: 60 }, align: "left" }, b: { align: "right" } }, question_breaks: ["b", "b"] });
state.basket = ["a", "b"];
assert.equal(sandbox.currentPrintOptions().option_layout, "vertical");
assert.equal(sandbox.currentPrintOptions().answer_space, "small");
assert.deepEqual(plain(sandbox.currentPrintOptions()).option_overrides, { a: "vertical", b: "auto" });
assert.deepEqual(plain(sandbox.currentPrintOptions()).answer_space_overrides, { a: "none", b: "large" });
assert.deepEqual(plain(sandbox.currentPrintOptions()).question_breaks, ["b"]);
// 配图调整和别的单题设置一个规矩：跟着这道题走，题移出卷子它就得跟着走。
assert.deepEqual(plain(sandbox.currentPrintOptions()).figure_overrides,
  { a: { width: { 0: 60 }, align: "left" }, b: { align: "right" } });
state.basket = ["a"];
assert.deepEqual(plain(sandbox.currentPrintOptions()).option_overrides, { a: "vertical" }, "Dropped publications must not remain in export overrides");
assert.deepEqual(plain(sandbox.currentPrintOptions()).answer_space_overrides, { a: "none" }, "Dropped writing-space overrides cannot enter an export");
assert.deepEqual(plain(sandbox.currentPrintOptions()).figure_overrides, { a: { width: { 0: 60 }, align: "left" } },
  "题移出卷子，它的图宽和位置也不能跟着进导出");
assert.deepEqual(plain(sandbox.currentPrintOptions()).question_breaks, []);
state.basket = [];
sandbox.prunePrintChoices();
state.basket = ["a", "b"];
assert.deepEqual(plain(sandbox.currentPrintOptions()).option_overrides, {}, "Clearing and re-adding questions must not revive old overrides");
assert.deepEqual(plain(sandbox.currentPrintOptions()).answer_space_overrides, {});
assert.deepEqual(plain(sandbox.currentPrintOptions()).figure_overrides, {}, "Clearing the basket must not revive figure adjustments either");
assert.deepEqual(plain(sandbox.currentPrintOptions()).question_breaks, []);
delete state.basket;

// 存回来的草稿要能照原样用回去：图宽是毫米、整行位置、点名换行，一样都不许丢。
// 存盘走 JSON，所以第几张图的序号回来是 "0" 而不是 0，两边都要当序号读。
sandbox.applyPrintOptions({ document: "questions", figure_overrides: {
  "1EF667E5-0000-0000-0000-000000000001": { width: { 0: 60.55 }, align: "left", breaks: [2, 1, 1] },
  "1EF667E5-0000-0000-0000-000000000002": { align: "center" },
  "1EF667E5-0000-0000-0000-000000000003": { width: { 0: 300 }, align: "top" },
} });
assert.deepEqual(plain(sandbox.currentPrintOptions()).figure_overrides, {
  "1EF667E5-0000-0000-0000-000000000001": { width: { 0: 60.6 }, align: "left", breaks: [1, 2] },
}, "只留下改过的那道题：宽到超出版心的、对不上位置的、跟默认一样的，都不算改过");
assert.deepEqual(plain(sandbox.normalizePrintOptions({ figure_overrides: {
  a: { width: { 0: 0, 1: 4.9, 2: 178, 3: 178.1, x: 40, "-1": 40, 4: "宽一点" }, breaks: [0, 3, 2, 3, -1, 1.5] },
  b: "nonsense", c: null,
} }).figure_overrides), { a: { width: { 2: 178 }, breaks: [2, 3] } },
  "5 毫米以下图就没了，178 毫米以上铺不下一张 A4，序号得是数字，换行点得是整数（第 1 张本来就在行首，不算换行）");
console.log("Figure adjustments ride along with the paper and survive a draft round trip: OK");

// No empty answer document or half-loaded paper can be offered for download.
controls.printDocument.value = "answers";
sandbox.syncExportButtons();
assert(controls.printButton.disabled && controls.exportPdf.disabled && controls.exportWord.disabled && controls.exportSplit.disabled);
controls.printDocument.value = "combined";
sandbox.syncExportButtons();
assert.equal(controls.printButton.disabled, false);
assert.equal(controls.exportWord.disabled, false);
assert.equal(controls.exportSplit.disabled, true);
printState.availableAnswers = 1;
printState.tooWide = 1;
sandbox.syncExportButtons();
assert.equal(controls.printButton.disabled, true);
assert.equal(controls.exportPdf.disabled, true);
assert.equal(controls.exportWord.disabled, false, "Word editing remains available when a formula cannot fit A4");
assert.equal(controls.exportSplit.disabled, false);
printState.tooWide = 0; printState.layoutPending = true;
sandbox.syncExportButtons(); assert.equal(controls.exportPdf.disabled, true);
printState.layoutPending = false; printState.layoutError = "有图表高于A4";
sandbox.syncExportButtons(); assert.equal(controls.exportPdf.disabled, true);

// 1.13.6: 「答案解析」在一道题都没载入时不能还是可点的。点了只会打开一个空编辑器，
// 屏幕上什么也不发生 —— 比灰掉难查得多。载入中也一样。
assert.equal(controls.managePrintAnswers.disabled, false, "有题载入时能补答案解析");
printState.loading = true;
sandbox.syncExportButtons();
assert.equal(controls.managePrintAnswers.disabled, true, "载入过程中不提供补答案解析");
printState.loading = false;
printState.items = [];
sandbox.syncExportButtons();
assert.equal(controls.managePrintAnswers.disabled, true, "一道题都没载入时不提供补答案解析");
assert.ok(controls.managePrintAnswers.title, "灰掉时要说清为什么");
printState.items = [{ id: "q1" }];
sandbox.syncExportButtons();
assert.equal(controls.managePrintAnswers.disabled, false, "题回来了，按钮就回来");
printState.layoutError = "";
printState.missing = [{ id: "withdrawn" }];
sandbox.syncExportButtons();
assert(controls.printButton.disabled && controls.exportWord.disabled && controls.exportSplit.disabled);

vm.runInContext(source.slice(source.indexOf("  const MISSING_REASONS ="), source.indexOf("  async function refreshBasket(")), sandbox);
assert.equal(sandbox.missingReason({ reason: "superseded" }), "题面改过，这道题已经有新版本了");
assert.equal(sandbox.missingReason({ reason: "withdrawn" }), "这道题已撤回");
assert.equal(sandbox.missingReason({ reason: "not_found" }), "这道题已不存在");
assert.equal(sandbox.missingReason({ reason: "superseded", message: "这道题已有新版本，请核对后选择当前版本" }),
  "这道题已有新版本，请核对后选择当前版本", "后端带来的中文说明优先于本地兜底");
for (const entry of [{ reason: "superseded" }, { reason: "withdrawn" }, { reason: "not_found" }, { reason: "who_knows" }])
  assert.ok(!/[a-z_]{4,}/.test(sandbox.missingReason(entry)), "界面上不能出现英文枚举：" + JSON.stringify(entry));
assert.ok(sandbox.missingReason(null).length > 0, "没有条目时也要有一句能看的话");

vm.runInContext(source.slice(source.indexOf("  function syncIndividualControls("), source.indexOf("  async function refreshPrintPages(")), sandbox);
printState.items = [{ id: "second", content: { options: { A: "1" } } }];
printState.optionOverrides = {}; printState.questionBreaks = []; printState.firstQuestion = "first";
controls.printIndividualQuestion.value = "second";
printState.exporting = true;
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualOption.disabled, true, "Reloading the paper during export must not re-enable mutable controls");
assert.equal(controls.printIndividualBreak.disabled, true);
assert.equal(controls.printIndividualSpace.disabled, true);
printState.exporting = false;
sandbox.syncIndividualControls(); assert.equal(controls.printIndividualOption.disabled, false); assert.equal(controls.printIndividualBreak.disabled, false);
printState.firstQuestion = "second"; sandbox.syncIndividualControls(); assert.equal(controls.printIndividualBreak.disabled, true, "The first question must not create an empty cover page");
printState.items[0].question_type = "free_response"; controls.printDocument.value = "questions";
printState.answerSpaceOverrides = { second: "none" }; sandbox.syncIndividualControls();
assert.equal(controls.printIndividualSpace.value, "none"); assert.equal(controls.printIndividualSpace.disabled, false);
controls.printDocument.value = "combined"; controls.printAnswerLayout.value = "inline"; sandbox.syncIndividualControls();
assert.equal(controls.printIndividualSpace.disabled, true, "Inline teacher layout disables per-question student space");

// 这道题没有配图，就不该出现配图调整；有了配图，每张图都得有一根滑块、一个毫米数
// 和一个「从下一行开始」，顺序跟预览里从上往下看到的一致。控件建不出来 = 老师点了
// 没反应，这一层必须在装之前就知道。
sandbox.syncIndividualControls();
assert.equal(controls.printFigureBox.hidden, true, "没有配图的题不该显示配图调整");
printState.items[0].content.figures = [{ slot: "stem" }, { slot: "A" }, { slot: "stem" }];
// 预览里每张图的宽度是排版时写死的内联样式，量回来就是这张图现在的毫米数。滑块得从
// 那个数起步：老师先看见「它现在多宽」，才知道往左还是往右拖，而不是先猜一个值。
// offsetWidth 用的是排版后的原始宽度，不能用 getBoundingClientRect —— 那个把整张卷子
// 的缩放也算进去了，量的是屏幕上的大小。
const shownFigure = stubElement();
shownFigure.className = "qb-figure";
const shownImage = stubElement();
shownImage.tagName = "img";
shownImage.offsetWidth = 240;
shownFigure.append(shownImage);
const shownRow = stubElement();
shownRow.className = "qb-figures";
shownRow.append(shownFigure);
const shownQuestion = stubElement();
shownQuestion.dataset = { questionId: "second" };
shownQuestion.append(shownRow);
ui.paper.querySelector = () => shownQuestion;
sandbox.syncIndividualControls();
assert.equal(controls.printFigureBox.hidden, false, "有配图的题要显示配图调整");
assert.equal(controls.printFigureList.children.length, 2, "只列题干配图，选项里的图不算");
assert.equal(controls.printFigureList.children[0].children[0].children[0].textContent, "第 1 张图");
assert.equal(controls.printFigureList.children[1].children[0].children[0].textContent, "第 2 张图");
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-slider").type, "range", "每张图都要有一根能拖的滑块");
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-slider").value, "64",
  "240px 是 63.5 毫米，滑块就该停在这儿，而不是从头到尾都用同一个默认值");
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-mm").value, "",
  "没调过的图不写死数字，空着才看得出「这一张还没动过」");
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-reset").hidden, true,
  "没调过的图没有可还原的东西");
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-mm").type, "number", "也要能直接填毫米数");
assert.ok(controls.printFigureList.children[0].querySelector(".print-figure-slider").title.length > 10,
  "看不懂的控件必须写清楚怎么用，鼠标停上去能看到说明");
// 老师改过之后，滑块和「还原」都要跟着变，不能还停在改之前的数上。
printState.figureOverrides = { second: { width: { 0: 90 }, align: "right" } };
sandbox.syncIndividualControls();
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-mm").value, "90");
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-slider").value, "90");
assert.equal(controls.printFigureList.children[0].querySelector(".print-figure-reset").hidden, false, "改过的图要能一键还原");
assert.equal(controls.printFigureAlign.value, "right");
printState.items[0].content.body_mode = "source_image";
sandbox.syncIndividualControls();
assert.equal(controls.printFigureBox.hidden, true, "整页扫描的原图题保留卷面排版，不给配图调整");
printState.missing = []; printState.tooWide = 0; printState.exporting = true;
sandbox.syncExportButtons();
assert(controls.printButton.disabled && controls.exportWord.disabled && controls.exportSplit.disabled);

vm.runInContext(source.slice(source.indexOf("  async function waitForPrintAssets("), source.indexOf("  function setExportBusy(")), sandbox);
vm.runInContext(source.slice(source.indexOf("  async function resolvePrintSolutions("), source.indexOf("  async function openPrint(")), sandbox);
(async () => {
  const original = { id: "a", content: { answer: "原卷 A", analysis: "原卷过程" }, solution: { id: "later-sync", answer: "之后同步 B", analysis: "新的步骤" } };
  printState.solutions = sandbox.solutions.draftSelections(undefined, ["a"]); printState.solutionRecords = new Map();
  let solutionFetches = 0; sandbox.fetch = async () => { solutionFetches++; throw new Error("An origin draft must not fetch a future overlay"); };
  const legacy = [{ ...original }]; await sandbox.resolvePrintSolutions(legacy);
  assert.equal(legacy[0].solution, null); assert.equal(legacy[0].solution_revision, "origin");
  assert.equal(solutionFetches, 0); assert.equal(original.solution.answer, "之后同步 B", "Per-paper origin choices do not alter the library snapshot");
  assert.equal(sandbox.solutions.selected(legacy[0]).content.answer, "原卷 A");
  // 1.13.5：打开预览时因为「当时没答案」自动补下的 origin 只是那一次的默认，不是选择。
  // 用户在题卡上填了答案，同一次使用里再打开组卷就该用新填的那份 ——
  // 否则界面上写着「已保存到题库」，试卷上还是「没有答案」，分别导出一直灰着。
  printState.solutions = {}; printState.solutionRecords = new Map(); printState.autoOrigin = new Set();
  const bare = [{ id: "a", content: {} }]; await sandbox.resolvePrintSolutions(bare);
  assert.equal(bare[0].solution_revision, "origin");
  assert(printState.autoOrigin.has("a"), "自动补上的 origin 要单独记一笔，别和草稿里存下来的选择混为一谈");
  const filled = [{ id: "a", content: {}, solution: { id: "just-saved", answer: "刚填的", analysis: "步骤" } }];
  await sandbox.resolvePrintSolutions(filled);
  assert.equal(filled[0].solution_revision, "just-saved");
  assert.equal(sandbox.solutions.selected(filled[0]).content.answer, "刚填的");
  assert(!printState.autoOrigin.has("a"), "换成真解析之后就不再是自动补的了");
  const fixed = { id: "fixed-revision", answer: "新编辑 C", analysis: "明确保存步骤" };
  printState.solutions.a = fixed.id; printState.solutionRecords.set(fixed.id, fixed);
  const edited = [{ ...original }]; await sandbox.resolvePrintSolutions(edited);
  assert.equal(edited[0].solution_revision, fixed.id); assert.equal(edited[0].solution.answer, "新编辑 C");
  assert.deepEqual(plain(sandbox.solutions.draftSelections(printState.solutions, ["a"])), { a: fixed.id }, "Saving a new edit replaces only this publication's origin marker");
  let decoded = false;
  const image = { loading: "lazy", naturalWidth: 400, decode: async () => {
    assert.equal(image.loading, "eager", "Offscreen lazy figures must start loading before decode");
    decoded = true;
  } };
  ui.paper.querySelectorAll = () => [image];
  await sandbox.waitForPrintAssets();
  assert.equal(decoded, true);
  ui.paper.querySelectorAll = () => [{ naturalWidth: 0, decode: async () => {} }];
  await assert.rejects(sandbox.waitForPrintAssets(), /配图未能载入/);
  ui.paper.querySelectorAll = () => [{ naturalWidth: 0, decode: async () => { throw new Error("broken image"); } }];
  await assert.rejects(sandbox.waitForPrintAssets(), /配图未能载入/);
  console.log("library export UI invariants: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
