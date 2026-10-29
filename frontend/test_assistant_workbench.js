"use strict";
// 题库页里的「助手工作台」（1.26.34）。
//
// 助手交上来的东西要在一处看、在一处点头。所以这一版有三件事钉死：
//   · 工作台住在题库页里，不另开一页 —— 上一轮自作主张加过独立页面，被退回来了。
//   · 没东西可看时整块藏起来；有待核对的就展开，老师不用自己去找这一块。
//   · 采纳之前题面一个字不动：只有老师点了「采纳这道改动」才发请求。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const markup = fs.readFileSync(require.resolve("./library.html"), "utf8");
const js = fs.readFileSync(require.resolve("./library.js"), "utf8");
const appJs = fs.readFileSync(require.resolve("./app.js"), "utf8");

// ── 一、工作台在题库页里，不是一个新页面 ──
assert.equal(markup.split('id="assistantWorkbench"').length - 1, 1, "工作台只能有一处");
assert.ok(markup.includes('id="assistantWorkbenchBody"'), "工作台得有地方装内容");
assert.match(markup, /<details id="assistantWorkbench" class="library-assistant-board" hidden>/,
  "工作台是折叠块，而且一开始藏着 —— 没东西可看时不占地方");
const boardAt = markup.indexOf('id="assistantWorkbench"');
const listAt = markup.indexOf('id="libraryList"');
assert.ok(boardAt > -1 && boardAt < listAt, "工作台住在题目列表上面，不是页面最后");
// 工作台不能是另一个页面：仓库里不该多出一个只放它的 HTML。
const pages = fs.readdirSync(__dirname).filter((name) => name.endsWith(".html")).sort();
assert.deepEqual(pages, ["index.html", "library.html", "practice.html"], "页面就这三张，别为工作台再开一张");

// ── 二、真的把它画出来 ──
class FakeNode {
  constructor(tag, className, text) {
    this.tag = tag; this.className = className || ""; this.textContent = text == null ? "" : String(text);
    this.children = []; this.hidden = false; this.open = false; this.disabled = false;
    this.listeners = {}; this.title = ""; this.type = "";
  }
  append(...nodes) { this.children.push(...nodes); return this; }
  replaceChildren(...nodes) { this.children = [...nodes]; return this; }
  addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
  querySelectorAll() { return []; }
  closest() { return null; }
}

function walk(node, out = []) { out.push(node); (node.children || []).forEach((child) => walk(child, out)); return out; }
function texts(node) { return walk(node).map((item) => item.textContent).filter(Boolean); }

const elements = {
  assistantWorkbench: new FakeNode("details"),
  assistantWorkbenchBody: new FakeNode("div"),
  assistantWorkbenchCount: new FakeNode("span"),
};
const requests = [];
const context = {
  state: { assistantBoard: null },
  node: (tag, className, text) => new FakeNode(tag, className, text),
  $: (id) => elements[id] || null,
  document: { querySelector: () => null },
  window: { setTimeout: undefined, clearTimeout: undefined },
  fetch: (url, options) => { requests.push({ url, options }); return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); },
  toast() {}, openQuestion() {}, load() { return Promise.resolve(); },
  QB: { TYPE_NAMES: { single_choice: "单选题", free_response: "解答题" } },
};

const start = js.indexOf("  const ASSISTANT_FIELD_LABELS = {");
const end = js.indexOf("  function render() {", start);
assert.ok(start > -1 && end > start, "切得到工作台那段源码");
vm.runInNewContext(js.slice(start, end), context);

// 没什么可看时：整块藏起来。
context.state.assistantBoard = { pending: { text: 0, tags: 0, answer: 0, total: 0 }, proposals: [], running: [], counts: {}, teacher: { here: false } };
context.renderAssistantWorkbench();
assert.equal(elements.assistantWorkbench.hidden, true, "没东西可看时整块要藏起来");
assert.equal(elements.assistantWorkbenchBody.children.length, 0, "藏起来时里面也要是空的");
assert.equal(requests.length, 0, "画界面不发请求 —— 只有老师点了才发");

// 有待核对的：块露出来、自动展开，每条一行两个按钮。
context.state.assistantBoard = {
  pending: { text: 1, tags: 0, answer: 0, total: 1 },
  proposals: [{
    id: "p-1", publication_id: "pub-1", number: 3, source_filename: "期中考试.pdf", agent: "豆包",
    changed: ["stem", "answer"], before: { stem: "原来的题干", answer: "原来的答案" },
    after: { stem: "新的题干", answer: "新的答案" },
  }],
  running: [{ job_id: "j-1", number: 5, kind: "tags", agent: "豆包" }],
  counts: { claimed: 1, done: 0, accepted: 0, rejected: 0 },
  teacher: { here: true, number: 7, state: "editing" },
};
context.renderAssistantWorkbench();
assert.equal(elements.assistantWorkbench.hidden, false, "有待核对的就要露出来");
assert.equal(elements.assistantWorkbench.open, true, "有待核对的要展开，老师不用自己找");
assert.equal(elements.assistantWorkbenchCount.textContent, "1 条待核对", "角标写清有几条待核对");
const body = elements.assistantWorkbenchBody;
const buttons = walk(body).filter((item) => item.tag === "button");
const labels = buttons.map((item) => item.textContent);
assert.ok(labels.includes("采纳这道改动"), "要有采纳的按钮");
assert.ok(labels.includes("不采纳这道改动"), "要有不采纳的按钮");
// 按钮名里要带被处理的对象，不能光一个「采纳」。
assert.ok(!labels.includes("采纳") && !labels.includes("不采纳"), "不能只写「采纳」两个字");
assert.ok(labels.includes("第 3 题"), "题号本身要能点，点了就打开那道题");
assert.ok(texts(body).join(" ").includes("豆包"), "要写清是谁交上来的");
assert.ok(texts(body).join(" ").includes("第 3 题"), "要写清是哪道题");
assert.ok(texts(body).join(" ").includes("原来的题干") && texts(body).join(" ").includes("新的题干"),
  "要能对着看「原来是这样 → 改成这样」");
assert.equal(requests.length, 0, "画界面仍然不发请求 —— 采纳之前题面一个字不动");

// ── 三、老师在场：审核页和题库页都要报一声 ──
assert.match(appJs, /function reportTeacherActivity\(questionId, mode\)/, "审核页要有报在场的那一段");
assert.match(appJs, /function setCurrent\(id, \{[\s\S]{0,400}reportTeacherActivity\(id\);/,
  "换题时要报一声，助手才知道老师正在看哪道题");
assert.match(appJs, /function openEditorPanel\(card, q,[\s\S]{0,400}reportTeacherActivity\(q\.id, "editing"\);/,
  "老师开始改字时要报「正在改」");
assert.match(js, /reportTeacherActivity\(item\.draft_id \|\| null, "editing"\)/,
  "题库页改题时也要报一声");

// ── 四、点了才会动：采纳和不采纳发的是不同的请求 ──
const actionButtons = walk(elements.assistantWorkbenchBody).filter((item) => item.tag === "button");
const acceptBtn = actionButtons.find((b) => b.textContent === "采纳这道改动");
const rejectBtn = actionButtons.find((b) => b.textContent === "不采纳这道改动");
assert.ok(acceptBtn && rejectBtn, "两个按钮都要在");

requests.length = 0;
acceptBtn.listeners.click[0]();
const acceptCall = requests.find((r) => String(r.url).includes("/decide"));
assert.ok(acceptCall, "点「采纳这道改动」要往处理提案的接口发请求");
assert.equal(acceptCall.options.method, "POST");
assert.equal(JSON.parse(acceptCall.options.body).action, "accept", "点采纳就发 accept，不能发成不采纳");
assert.ok(String(acceptCall.url).includes("p-1"), "要带上是哪一条提案");

requests.length = 0;
rejectBtn.listeners.click[0]();
const rejectCall = requests.find((r) => String(r.url).includes("/decide"));
assert.ok(rejectCall, "点「不采纳这道改动」也要发请求");
assert.equal(JSON.parse(rejectCall.options.body).action, "reject", "点不采纳就发 reject");
