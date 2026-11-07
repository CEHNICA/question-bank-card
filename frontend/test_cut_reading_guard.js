"use strict";
// 1.26.47：「AI 识读这题」灰掉的时候，必须说得清「为什么灰、下一步去哪」。
//
// 这个测试**跑真的代码**：从 app.js 里把 cutReadingAction 原样抠出来，用 vm
// 真跑一遍，断言按钮真的 disabled、旁边那行常驻小字真的挂上去了 ——
// 不是检查源码里有没有那句话。上一轮的教训：光验话术函数，函数写得再对，
// 按钮上没挂上去，测试照样绿。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const Wording = require("./control-wording.js");
global.QBControlWording = Wording;
const Cut = require("./app.js");

class Node {
  constructor(tag = "div", cls = "", text = "") {
    this.tagName = tag.toUpperCase(); this.className = cls; this.textContent = text;
    this.children = []; this.disabled = false; this.title = ""; this.hidden = false;
  }
  append(...nodes) { nodes.forEach((n) => { if (typeof n === "string") n = new Node("text", "", n); n.parent = this; this.children.push(n); }); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  addEventListener() {}
  setAttribute(key, value) { this[key] = String(value); }
}

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

// ---- 把 cutReadingAction 从 app.js 里原样抠出来 --------------------------------
const start = js.indexOf("  function cutReadingAction(");
assert.ok(start > 0, "app.js 里得有 cutReadingAction");
const rest = js.slice(start);
const end = rest.indexOf("\n  function ", 10);
const fn = rest.slice(0, end > 0 ? end : rest.length);
assert.match(fn, /cutReadingGuard/, "这个函数必须按后端给的判定来判灰");

function runAction(q, overrides = {}) {
  const asked = [];
  const context = {
    QBControlWording: Wording,
    QBCutReading: Cut,
    state: { paperId: "paper-test", paper: { status: "ready", demo: false } },
    questionReadingRequests: new Set(), cutReadingRequests: new Set(), cutReadingStops: new Set(),
    readerUnavailable: () => null,
    rereadQuestion: (question) => { asked.push(question); },
    el: (tag, cls, text) => new Node(tag, cls, text),
    button: (label, cls, onClick, title) => {
      const node = new Node("button", cls, label);
      if (title) node.title = title;
      node.onClick = onClick;
      return node;
    },
    q,
    ...overrides,
  };
  vm.runInNewContext(fn + "\nglobalThis.__result = cutReadingAction(q);", context);
  return { ...context.__result, asked };
}

const card = (extra = {}) => ({ id: 1, number: 1, body_mode: "source_image", content_revision: 4,
  regions: [{ page_idx: 0, bbox: [10, 20, 800, 300] }], ...extra });

// ---- 判据：后端给什么，页面就认什么 ------------------------------------------
assert.deepEqual(Cut.cutReadingGuard(card()), { blocked: false, reason: "" });
assert.deepEqual(Cut.cutReadingGuard(card({ cut_reading_guard: { blocked: true, reason: "published" } })),
  { blocked: true, reason: "published" });
assert.deepEqual(Cut.cutReadingGuard(card({ cut_reading_guard: { blocked: false, reason: "" } })),
  { blocked: false, reason: "" });

// ---- 批量识读：被保护的题不再进候选（后端本来也会逐条跳过） --------------------
const batch = [card({ id: 1 }), card({ id: 2, cut_reading_guard: { blocked: true, reason: "published" } }),
  card({ id: 3, cut_reading_guard: { blocked: true, reason: "manual_text" } })];
assert.deepEqual(Cut.cutReadingSummary(batch).eligibleIds, [1], "受保护的题不进批量候选");

// ---- 灰掉的按钮：真的灰了，而且说清了为什么灰、下一步去哪 --------------------
for (const [reason, expect] of [
  ["approved", /审核/],
  ["published", /题库/],
  ["manual_text", /文字/],
]) {
  const { read, hint } = runAction(card({ cut_reading_guard: { blocked: true, reason } }));
  assert.equal(read.disabled, true, `${reason} 的题不该还能点`);
  assert.match(read.textContent, /不再识读/, `${reason} 的按钮本身要写出为什么灰`);
  assert.ok(read.title && read.title.length > 8, `${reason} 的按钮要有悬停说明`);
  assert.match(read.title, expect, `${reason} 的悬停说明要说清是哪一条`);
  assert.ok(hint, `${reason} 的题旁边要有一行常驻小字`);
  assert.match(hint.textContent, expect, `${reason} 的常驻小字要说清是哪一条`);
  assert.match(hint.textContent, /改字|撤销|题库/, `${reason} 的常驻小字要说下一步去哪`);
  assert.match(hint.className, /cut-reading-guard/, "常驻小字要认得出是护栏说明");
}

// ---- 没被保护的题：按钮照常用，也不该多出一行说明 ----------------------------
{
  const { read, hint, asked } = runAction(card());
  assert.equal(read.disabled, false, "能识读的题不该是灰的");
  assert.equal(read.textContent, "AI 识读这题");
  assert.equal(hint, null, "没被挡住就不用多写一行");
  assert.equal(typeof read.onClick, "function", "按钮必须真的接到「重新识读」上");
  read.onClick();
  assert.equal(asked.length, 1, "点一下就得真的去识读");
}

// ---- 1.26.51：撤回过的入库记录不再挡识读 ---------------------------------------
// 真库实测 392 道原图题就是被这个挡住的：入库记录已撤回、题库里一条活的都没有，
// 快照与当前题逐字相同 —— 那里头根本没有人工成果，不该挡。
{
  const { read, hint, asked } = runAction(card({
    cut_reading_guard: { blocked: false, reason: "" },
  }));
  assert.equal(read.disabled, false, "撤回过的入库记录不该让按钮变灰");
  assert.equal(hint, null, "没被挡住就不用多写一行");
  read.onClick();
  assert.equal(asked.length, 1, "这道题点一下就得真的去识读");
}

// ---- 话术集中在 control-wording.js：三种理由都要有话 --------------------------
for (const reason of ["approved", "published", "manual_text"]) {
  const text = Wording.cutReadingGuardText(reason);
  assert.ok(text && text.label && text.title && text.brief, `${reason} 要有 label/title/brief`);
  assert.match(text.brief, /改字|撤销|题库/, `${reason} 的 brief 要说下一步去哪`);
}
assert.equal(Wording.cutReadingGuardText("approved").label, Wording.CUT_READING.approved.label);
assert.ok(Wording.cutReadingGuardText("没听说过的理由").brief, "没听说过的理由也得有话");

// ---- 接线：题卡真的用上了这个函数，不是各写一份 ------------------------------
assert.match(js, /const cut = cutReadingAction\(q\);/, "题卡必须调用 cutReadingAction");
assert.match(js, /if \(cut\.hint\) actions\.append\(cut\.hint\);/, "那行常驻小字必须真的插进题卡");
assert.doesNotMatch(js, /read\.disabled = Boolean\(q\.ocr_pending[\s\S]{0,400}q\.publication/,
  "不能再只按已审核和在用的入库记录判灰 —— 改过字的题也算，要听后端给的判定");

// ---- 单题重读：受保护的题不提交那个注定被拒的请求 ----------------------------
(async () => {
  const single = js.slice(js.indexOf("  async function rereadQuestion("), js.indexOf("  function updatePaperFromResponse("));
  async function submit(q) {
    const requests = [];
    const context = {
      state: { paperId: "paper-test", paper: { status: "ready", demo: false } },
      questionReadingRequests: new Set(), cutReadingRequests: new Set(), cutReadingStops: new Set(),
      QBCutReading: Cut,
      QBRegionWait: { boundedRequest: (task) => task(undefined) },
      api: async (url) => { requests.push(url); return {}; },
      questionById: () => q, applyQuestion() {}, refreshPaper: async () => {},
      toast() {}, renderReadingControls() {}, confirmDialog: async () => true,
      q,
    };
    await vm.runInNewContext(single + "\nrereadQuestion(q);", context);
    return requests;
  }
  const guarded = card({ cut_reading_guard: { blocked: true, reason: "published" } });
  assert.deepEqual(await submit(guarded), [], "受保护的题不该再发那个注定被 409 的请求");
  assert.equal((await submit(card())).length, 1, "没被保护的题照常提交识读");

  console.log("Cut-reading guard: the button greys out and says why: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
