"use strict";
// 组卷预览里有四个控件按不了的时候，页面上一个字都不说为什么：
//   ·「分别导出题目卷与答案卷」灰掉，悬停说明是空的（实机量到 title=''），
//     而同一个屏幕上的「答案解析」一直带着说明；
//   ·「输出内容」里锁着的「答案与解析」那一项，被锁的原生选项连悬停都不给；
//   ·「本题留白」灰掉没有任何说明，而下面那句提示讲的是「这题另起一页」，
//     是另一件事；
//   ·「更多排版」「调整单题」两个折叠条只有 20px，隔壁「清空试题篮」34px。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const markup = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");

// 名字要从页面里抠出来，不要另抄一份。源码里改了名，另抄的那份会安静地对不上。
const splitName = markup.match(/id="exportSplit"[^>]*>([^<]+)</);
assert.ok(splitName, "「分别导出题目卷与答案卷」这个按钮还在");
const answersName = markup.match(/<option value="answers">([^<]+)</);
assert.ok(answersName, "「输出内容」里的「答案与解析」这一项还在");
const aiName = markup.match(/id="printAiBox"[\s\S]*?<span class="switch-track"[^>]*><\/span>([^<]+)</);
assert.ok(aiName, "「附 AI 参考」那个开关还在");
const manageName = markup.match(/id="managePrintAnswers"[^>]*>([^<]+)</);
assert.ok(manageName, "顶栏的「答案解析」按钮还在");

const controls = Object.fromEntries([
  "printDocument", "printAnswerLayout", "printAnswerSpace", "exportSplit",
  "printButton", "exportPdf", "exportWord", "printOptionLayout",
  "printIndividualQuestion", "printIndividualOption", "printIndividualSpace",
  "printIndividualBreak", "printIndividualHint", "printAnswerStatus",
  "printAnswerLayoutBox", "managePrintAnswers"
].map((id) => [id, { value: "", checked: false, disabled: false, hidden: false, title: "", textContent: "" }]));
controls.printAnswerStatus.setAttribute = () => {};
// syncPrintAnswers 会去把「答案与解析」这一项锁上，得给这个下拉一个真的能问出选项的壳。
controls.printDocument.querySelector = () => ({ disabled: false });
const answersOption = { disabled: false };
controls.printDocument.querySelector = () => answersOption;

const ui = { printAnswers: { checked: false, disabled: false, setAttribute: () => {} },
  printAi: { checked: false }, printAiBox: { hidden: true },
  paper: { querySelectorAll: () => [] }, sheet: { querySelector: () => ({ append: () => {} }) } };
const state = { features: { ai_answer: true } };
const printState = { items: [], missing: [], loading: false, exporting: false, availableAnswers: 0,
  tooWide: 0, layoutPending: false, layoutError: "", optionOverrides: {}, answerSpaceOverrides: {},
  figureOverrides: {}, questionBreaks: [], firstQuestion: "" };
const sandbox = {
  $: (id) => controls[id], ui, state, printState,
  solutions: require("./library-solutions.js"),
  printAnswersPreference: true, renderFigureControls: () => {}, groupedQuestionNumber: (item) => item.exam_number ?? 1,
  console, setTimeout, clearTimeout
};
vm.createContext(sandbox);
const slice = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to);
  assert.ok(start > -1 && end > start, `切片锚点没了：${from} → ${to}`);
  return source.slice(start, end);
};
// 三段都自带「锚点必须存在」的断言 —— indexOf 返回 -1 时 slice 不报错，
// 会安静地跑一个空沙箱然后全绿。
vm.runInContext(slice("  function syncExportButtons(", "  async function resolvePrintSolutions("), sandbox);
vm.runInContext(slice("  function syncIndividualControls(", "  function stemFiguresOf("), sandbox);
assert.equal(typeof sandbox.syncExportButtons, "function", "syncExportButtons 切进来了");
assert.equal(typeof sandbox.syncPrintAnswers, "function", "syncPrintAnswers 切进来了");
assert.equal(typeof sandbox.syncIndividualControls, "function", "syncIndividualControls 切进来了");

const withoutAnswer = () => ({ content: { answer: "", analysis: "" }, ai_answer: { answer: "AI 参考", analysis: "" } });
const withAnswer = () => ({ content: { answer: "原卷答案", analysis: "原卷过程" } });

// ── 1. 「分别导出题目卷与答案卷」：四种情况都得有一句说法，而且彼此不能是同一句 ──
const titles = new Map();
printState.items = [withAnswer()];
printState.availableAnswers = 1;
sandbox.syncExportButtons();
titles.set("正常", controls.exportSplit.title);
assert.equal(controls.exportSplit.disabled, false, "有答案时这个按钮是能按的");
assert.match(titles.get("正常"), /两份/, "说得通时也要说清按下去会得到什么");

printState.availableAnswers = 0;
sandbox.syncExportButtons();
titles.set("没答案", controls.exportSplit.title);
assert.equal(controls.exportSplit.disabled, true);
assert.match(titles.get("没答案"), /答案/, "灰掉的理由得落在「没有答案」上");
assert.doesNotMatch(titles.get("没答案"), /两份文件/, "按不下去的时候不能还说会导出两份");

printState.missing = [{ id: "x" }];
sandbox.syncExportButtons();
titles.set("有题没载入", controls.exportSplit.title);
assert.match(titles.get("有题没载入"), /没载入/, "理由是「有题没载入」，不是「没有答案」");
assert.doesNotMatch(titles.get("有题没载入"), /还没有答案/, "两个理由不能串台");
printState.missing = [];

printState.items = [];
sandbox.syncExportButtons();
titles.set("一道题都没载入", controls.exportSplit.title);
assert.match(titles.get("一道题都没载入"), /未载入/, "理由得是「先把题处理掉」");
assert.doesNotMatch(titles.get("一道题都没载入"), /还没有答案/, "一道题都没有时说「没有答案」是胡话");

assert.equal(new Set(titles.values()).size, titles.size, "四种情况说了四句话，不能有两句一样的");
for (const [why, title] of titles) assert.ok(title && title.trim(), `${why}：不能一句都不说`);

// ── 2. 状态行要点名那两个按不了的入口，并且把出路说清楚 ──
printState.items = [withoutAnswer(), withoutAnswer()];
ui.printAi.checked = false;
sandbox.syncPrintAnswers(printState.items);
// 整句比对，不拿「整段话里出现过这个名字」当证据 —— 上面那句本来就带着「附 AI 参考」，
// 断言松一点就会拿它冒充出路那句点名了那个开关。
const aiEscape = `「${splitName[1]}」和「输出内容」里的「${answersName[1]}」要等打开上面的「${aiName[1]}」才能用`;
assert.ok(controls.printAnswerStatus.textContent.includes(aiEscape),
  "出路那一句要点名两个按不了的入口和那个开关");
assert.equal(controls.exportSplit.disabled, true, "AI 参考还没开时它确实是灰的");

ui.printAi.checked = true;
sandbox.syncPrintAnswers(printState.items);
assert.equal(controls.exportSplit.disabled, false, "照着状态行说的做了，按钮真的解禁了");
assert.doesNotMatch(controls.printAnswerStatus.textContent, new RegExp(`「${splitName[1]}」`),
  "解禁之后就别再抱怨了 —— 说了没用的话，下次就不信了");

ui.printAi.checked = false;
state.features.ai_answer = false;
sandbox.syncPrintAnswers(printState.items);
const noAiEscape = `「${splitName[1]}」和「输出内容」里的「${answersName[1]}」都用不了，先在顶栏的「${manageName[1]}」里补上答案`;
assert.ok(controls.printAnswerStatus.textContent.includes(noAiEscape),
  "AI 参考用不上时，得点名真正管用的那个入口");
assert.doesNotMatch(controls.printAnswerStatus.textContent, new RegExp(`「${aiName[1]}」`),
  "开关根本不在屏上，不能让人去找");

sandbox.syncPrintAnswers([]);
assert.doesNotMatch(controls.printAnswerStatus.textContent, new RegExp(`「${splitName[1]}」`),
  "篮子空着的时候不是「没答案」，别用同一句话");

state.features.ai_answer = true;
printState.items = [withAnswer(), withAnswer()];
sandbox.syncPrintAnswers(printState.items);
assert.doesNotMatch(controls.printAnswerStatus.textContent, new RegExp(`「${splitName[1]}」`),
  "题都有答案时那句话整个不该出现");

// ── 3. 「本题留白」和「这题另起一页」是两个互不相干的原因，各说各的 ──
printState.firstQuestion = "q1";
printState.items = [{ id: "q1", question_type: "choice", content: { body_mode: "text" } },
  { id: "q2", question_type: "free_response", content: { body_mode: "text" } }];
controls.printDocument.value = "questions";
controls.printIndividualQuestion.value = "q1";
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualBreak.disabled, true, "首题用不着另起一页");
assert.match(controls.printIndividualBreak.title, /第一页/, "得说是因为它在第一页");
assert.equal(controls.printIndividualSpace.disabled, true, "选择题不留白");
assert.match(controls.printIndividualSpace.title, /解答题/, "留白只对解答题有用，这句得说出来");

controls.printIndividualQuestion.value = "q2";
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualSpace.disabled, false, "解答题可以留白");
assert.doesNotMatch(controls.printIndividualSpace.title, /只对解答题/, "能用了就别还说它不能用");
assert.equal(controls.printIndividualBreak.disabled, false);
assert.doesNotMatch(controls.printIndividualBreak.title, /第一页/, "能用了就别还说它在第一页");

controls.printDocument.value = "answers";
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualSpace.disabled, true, "只出答案卷时没有留白");
assert.doesNotMatch(controls.printIndividualSpace.title, /只对解答题/, "这次的原因是输出内容，不是题型");
// 光说「不是那个原因」不够，得说对原因 —— 漏过一次变异：只留 doesNotMatch 时，
// 一个什么都不解释的标题照样过关。
assert.match(controls.printIndividualSpace.title, new RegExp(answersName[1]),
  "理由得是「输出内容选了答案与解析」");

controls.printDocument.value = "combined";
controls.printAnswerLayout.value = "inline";
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualSpace.disabled, true, "答案紧跟题后时不留白");
assert.match(controls.printIndividualSpace.title, /紧跟/, "理由得是「答案紧跟在题目后面」");

controls.printAnswerLayout.value = "appendix";
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualSpace.disabled, false, "答案放卷末时能留白");
assert.doesNotMatch(controls.printIndividualSpace.title, /紧跟|答案与解析/, "能用了就别还说不能");
controls.printDocument.value = "questions";

controls.printIndividualQuestion.value = "";
sandbox.syncIndividualControls();
assert.equal(controls.printIndividualSpace.disabled, true, "没选题时两个都得锁住");
assert.match(controls.printIndividualSpace.title, /选一道题/, "得告诉人先选哪一道");
assert.match(controls.printIndividualBreak.title, /选一道题/);

// ── 4. 两个折叠条得有高度，斜邻的「清空试题篮」是 34px ──
const rule = css.match(/\.print-advanced > summary[^{]*\{([^}]*)\}/);
assert.ok(rule, "那两个折叠条的样式还在");
assert.match(rule[1], /min-height:\s*30px/, "20px 的点击区太小");
assert.doesNotMatch(rule[1], /display:\s*(flex|grid)/,
  "改成 flex 或 grid 会让 Chrome 把折叠三角去掉，而全应用的折叠条都有三角");
assert.ok(rule[0].includes(".print-individual > summary"), "两个折叠条要一起改，只改一个还是一个矮");

console.log("Greyed controls in the print preview say why they are grey, and point at the way out: OK");
