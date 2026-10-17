"use strict";
// 组卷预览剩下没细看的两处，走真人路线量出来的两件事：
//   · 把「输出内容」选成「答案与解析」、再把 AI 参考关掉，答案一下子全没了，「导出 Word」
//     和「导出 PDF」同时灰掉，title 是空的、鼠标是 not-allowed。1.25.15 只给「分别导出题目
//     卷与答案卷」补了说明，把这两颗最常按的主按钮漏了。
//   · 没打开任何草稿时，「保存草稿」和「复制为新草稿」发的是同一个请求 —— 同一个操作摆
//     两个入口，第一次用的人只会愣一下。真打开了一份草稿，那颗按钮才有意义。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const markup = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");

const nameOf = (id) => {
  const hit = markup.match(new RegExp(`id="${id}"[^>]*>([^<]+)<`));
  assert.ok(hit, `${id} 这个控件还在`);
  return hit[1];
};
const saveName = nameOf("saveDraft");
const copyName = nameOf("saveDraftAs");
assert.notEqual(saveName, copyName, "两颗按钮的名字本来就得不一样，否则没人分得清");
const answersName = markup.match(/<option value="answers">([^<]+)</)[1];

const controls = Object.fromEntries([
  "printButton", "exportPdf", "exportWord", "exportSplit", "managePrintAnswers", "printOptionLayout",
  "printDocument", "printAnswerLayout", "printFontSize", "printStudentInfo", "printPagination",
  "printOrigin", "printAi", "saveDraft", "saveDraftAs", "draftSaveStatus", "printAnswerSpace"
].map((id) => [id, { value: "", checked: false, disabled: false, hidden: false, title: "", textContent: "" }]));
controls.printDocument.value = "questions";
controls.printFontSize.value = "12";
controls.printAnswerSpace.value = "small";
controls.printPagination.value = "compact";
controls.printOptionLayout.value = "auto";
controls.printAnswerLayout.value = "appendix";
controls.printStudentInfo.checked = true;

const ui = { printTitle: { value: "第一章 摸底" }, printOrigin: { checked: false }, printAi: { checked: false } };
const state = { features: { ai_answer: true }, draft: null, basket: ["a"], draftBaseline: null, draftDirty: false };
const printState = { items: [{ content: { body_mode: "text" } }], missing: [], loading: false, exporting: false,
  availableAnswers: 2, tooWide: 0, layoutPending: false, layoutError: "", optionOverrides: {},
  answerSpaceOverrides: {}, figureOverrides: {}, questionBreaks: [], solutions: {} };
let saved = null;
const sandbox = {
  $: (id) => controls[id], ui, state, printState, console,
  solutions: require("./library-solutions.js"),
  toast: () => {},
  // 存草稿真的走一遍网络：只有真存了，state.draft 才会变，复制那颗才该冒出来。
  fetch: async (url) => { saved = { url }; return { ok: true, json: async () => ({ draft: { id: "d-new", title: "第一章 摸底", ids: ["a"], revision: 1 } }) }; },
  encodeURIComponent
};
vm.createContext(sandbox);
const slice = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to);
  assert.ok(start > -1 && end > start, `切片锚点没了：${from} → ${to}`);
  return source.slice(start, end);
};
vm.runInContext(slice("  function normalizePrintOptions(", "  function printAnswerContent("), sandbox);
vm.runInContext(slice("  function syncDraftButtons(", "  async function saveDraft("), sandbox);
vm.runInContext(slice("  async function saveDraft(", "  async function openDrafts("), sandbox);
for (const name of ["syncDraftButtons", "markDraftDirty", "syncExportButtons", "saveDraft"]) {
  assert.equal(typeof sandbox[name], "function", `${name} 切进来了`);
}

// ── 1. 三颗导出按钮：每种灰因都得说对，且彼此不能是同一句 ──
const titlesOf = () => Object.fromEntries(["printButton", "exportWord", "exportPdf"].map((id) => [id, controls[id].title]));
const noItems = { items: [], missing: [], loading: false, exporting: false, availableAnswers: 0 };

Object.assign(printState, noItems, { layoutPending: false, layoutError: "" });
sandbox.syncExportButtons();
assert.ok(printState.items.length === 0 && controls.exportWord.disabled, "篮子空时导出是灰的");
assert.match(controls.exportWord.title, /未载入/, "空篮子的理由是「先把选题处理掉」");

Object.assign(printState, { items: [{ content: { body_mode: "text" } }], availableAnswers: 2, missing: [] });
sandbox.syncExportButtons();
const ready = titlesOf();
for (const [id, title] of Object.entries(ready)) assert.ok(title && title.trim(), `${id} 能用时也要说按下去会怎样`);
assert.match(ready.exportWord, /继续编辑/, "Word 那颗要说明能接着改");
assert.match(ready.exportPdf, /PDF/, "PDF 那颗要说明拿到的是 PDF");
assert.doesNotMatch(ready.exportWord, /PDF/, "两颗按钮不能共用一句话");

printState.availableAnswers = 0;
controls.printDocument.value = "answers";
sandbox.syncExportButtons();
assert.ok(controls.exportWord.disabled && controls.exportPdf.disabled && controls.printButton.disabled,
  "选了答案卷却没答案，三颗一起灰");
const answerWhy = titlesOf();
for (const [id, title] of Object.entries(answerWhy)) {
  assert.match(title, new RegExp(answersName), `${id} 得说出「选了答案与解析」这件事`);
  assert.match(title, /答案/, `${id} 还得说题没答案`);
}
assert.doesNotMatch(answerWhy.exportPdf, /继续编辑/, "不能灰着还说「可继续编辑」");
controls.printDocument.value = "questions";
printState.availableAnswers = 2;

printState.missing = [{ id: "gone" }];
sandbox.syncExportButtons();
assert.match(controls.exportWord.title, /1 道题没载入/, "有题没载入，数得出来");
printState.missing = [];

printState.tooWide = 1;
sandbox.syncExportButtons();
assert.equal(controls.exportPdf.disabled, true, "有图表高过 A4，PDF 出不来");
assert.equal(controls.exportWord.disabled, false, "Word 不受这个影响");
assert.match(controls.exportPdf.title, /A4/, "PDF 得说清是版面放不下");
assert.doesNotMatch(controls.exportWord.title, /A4/, "Word 没这个问题，别拿它的理由说事");
// 打印那颗同样会灰，同样得说话 —— 漏过一条变异才发现只测了 PDF。
assert.equal(controls.printButton.disabled, true, "打印也被版面挡住了");
assert.match(controls.printButton.title, /A4/, "打印也得说清是版面放不下");

printState.tooWide = 0; printState.layoutError = "有图表高于 A4";
sandbox.syncExportButtons();
assert.equal(controls.exportPdf.disabled, true);
assert.ok(controls.exportPdf.title.includes("有图表高于 A4"), "版面报什么错，悬停就说那条错");
assert.ok(controls.printButton.title.includes("有图表高于 A4"), "打印那颗也得报那条错");
printState.layoutError = "";

printState.exporting = true;
sandbox.syncExportButtons();
assert.match(controls.exportPdf.title, /正在导出|请稍等/, "正在导出时要说在忙什么");
printState.exporting = false;
sandbox.syncExportButtons();

// ── 2. 草稿：没打开草稿时，两个入口只留一个 ──
// 先看页面本身。刚才那一版是把「复制为新草稿」默认显示、等同步函数去藏它 —— 实机复验
// 量到刚打开预览那一帧它还露着、悬停是空的，因为那一刻没人同步过。测试里直接调同步
// 函数，正好绕过了这一帧。改成默认就藏着，错的初始状态根本出不来。
const copyTag = markup.match(/<button id="saveDraftAs"[^>]*>/)[0];
assert.match(copyTag, /\shidden\b/, "「复制为新草稿」在页面里就该默认藏着，不能等同步函数去藏");
// 「保存草稿」那一句同理：刚打开预览那一刻没人同步过，悬停会是空的。把默认说法写进页面，
// 错的初始状态就出不来。同步函数在真的打开了草稿时会改写它。
const saveTag = markup.match(/<button id="saveDraft"[^>]*>/)[0];
const defaultSaveTitle = saveTag.match(/\stitle="([^"]*)"/);
assert.ok(defaultSaveTitle, "「保存草稿」在页面里就得带着默认悬停说明");
sandbox.syncDraftButtons();
// 页面里用的是弯引号，脚本里用的是直角引号，比之前先把成对的引号统一了。
const corner = (text) => text.replace(/“([^”]*)”/g, "「$1」");
assert.equal(corner(defaultSaveTitle[1]), controls.saveDraft.title,
  "页面里写的默认说法，要和同步函数在没有草稿时说的完全一样 —— 两处不一致，以后改一处就穿了");

state.draft = null;
sandbox.syncDraftButtons();
assert.equal(controls.saveDraftAs.hidden, true, "没打开草稿时，复制那颗不出现");
assert.equal(controls.saveDraft.disabled, false, "保存那颗一直在");
// 悬停不用把按钮名再抄一遍（按钮上就写着），但得说清动的是「这次组卷」，以及存到哪去。
assert.match(controls.saveDraft.title, /这次组卷/, "得说清存的是这次组的东西");
assert.match(controls.saveDraft.title, /草稿/, "得说清存下来之后叫什么");
assert.doesNotMatch(controls.saveDraft.title, new RegExp(copyName), "说的是保存，别混成复制");

state.draft = { id: "d1", title: "第三章 单元过关" };
sandbox.syncDraftButtons();
assert.equal(controls.saveDraftAs.hidden, false, "真打开了草稿，复制那颗才有意义，这时才出现");
assert.match(controls.saveDraft.title, /覆盖/, "保存 = 覆盖原稿，得说清");
assert.match(controls.saveDraft.title, /第三章 单元过关/, "得点名覆盖的是哪一份");
assert.match(controls.saveDraftAs.title, /另存/, "复制那颗得说清是另存一份");
assert.match(controls.saveDraftAs.title, /不动/, "复制那颗得说清原来那份不会被动");
assert.match(controls.saveDraftAs.title, /第三章 单元过关/, "得点名动的哪一份");
assert.notEqual(controls.saveDraft.title, controls.saveDraftAs.title, "两颗不能是同一句话");

// 删掉正在编辑的草稿后，那颗按钮要跟着消失 —— 走的是 markDraftDirty 这条路
state.draftDirty = true;
sandbox.markDraftDirty();
assert.equal(controls.saveDraftAs.hidden, false, "草稿还在，复制那颗就该在");
state.draft = null;
sandbox.markDraftDirty();
assert.equal(controls.saveDraftAs.hidden, true, "草稿删了，复制那颗必须跟着消失");
assert.match(controls.draftSaveStatus.textContent, /尚未保存为草稿/, "顺带把状态那句也更新了");

// 真存一次：存完之后当前就有了草稿，复制那颗必须马上冒出来，不能等下一次渲染。
// 漏过一条变异才发现这里原先根本没测 —— 只测了「打开草稿」和「删掉草稿」两条路。
(async () => {
  state.draft = null;
  state.basket = ["a"];
  sandbox.syncDraftButtons();
  assert.equal(controls.saveDraftAs.hidden, true, "还没存过，复制那颗不出现");
  await sandbox.saveDraft();
  assert.ok(saved && saved.url.includes("/api/library/drafts"), "真的发出去一次保存请求");
  assert.ok(state.draft && state.draft.id === "d-new", "保存之后当前草稿就是刚存的那份");
  assert.equal(controls.saveDraftAs.hidden, false, "刚存完就有草稿了，复制那颗该立刻出现");
  assert.match(controls.saveDraftAs.title, /第一章 摸底/, "那颗出现时得说得清动的是哪一份");
  assert.match(controls.saveDraft.title, /覆盖/, "再点保存就变成覆盖原稿，悬停得跟着变");
  console.log("Export buttons say why they are grey, and the draft menu keeps one entry per operation: OK");
})();
