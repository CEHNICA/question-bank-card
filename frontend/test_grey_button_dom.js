"use strict";
// 1.26.37：面板里灰掉的按钮，鼠标停上去必须能知道「为什么灰」和「下一步去哪」。
//
// 这个测试**开真的面板、点真的按钮、看真的 title 属性** —— 不是检查源码里
// 有没有那句话。上一轮的教训：光验话术函数，函数写得再对，按钮上没挂上去，
// 测试照样绿。所以这里必须落到真实 DOM 上。
//
// 用的是 test_original_paper_layout.js 里那套 DOM harness（同样的小 Node）。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const L = require("./original-paper-layout.js");

const r = (page, bbox) => ({ page_idx: page, bbox });
const pages = [{ page_idx: 0 }, { page_idx: 1 }, { page_idx: 2 }];

class Node {
  constructor(tag = "div", cls = "", text = "") {
    this.tagName = tag.toUpperCase(); this.className = cls; this.textContent = text; this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = new Map(); this.hidden = false; this.disabled = false; this.isConnected = true;
    this.style = { setProperty: (key, value) => { this.style[key] = value; } };
    this.classList = { add: (...xs) => xs.forEach((x) => this.classList.toggle(x, true)), remove: (...xs) => xs.forEach((x) => this.classList.toggle(x, false)), contains: (x) => this.className.split(" ").includes(x), toggle: (x, value) => { const s = new Set(this.className.split(" ").filter(Boolean)); const yes = value === undefined ? !s.has(x) : value; if (yes) s.add(x); else s.delete(x); this.className = [...s].join(" "); return yes; } };
  }
  append(...nodes) { nodes.forEach((n) => { if (typeof n === "string") n = new Node("text", "", n); n.parent = this; this.children.push(n); }); }
  replaceChildren(...nodes) { this.children.forEach((n) => { n.parent = null; }); this.children = []; this.append(...nodes); }
  addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(fn); }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  async emit(type, event = {}) { const e = { target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...event }; for (const fn of [...(this.listeners.get(type) || [])]) await fn(e); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return selector.slice(1).split(".").every((x) => this.classList.contains(x));
    const data = selector.match(/^\[data-([a-z-]+)(?:="([^"]*)")?\]$/);
    if (data) { const key = data[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase()); return key in this.dataset && (data[2] === undefined || String(this.dataset[key]) === data[2]); }
    return this.tagName === selector.toUpperCase();
  }
  closest(selector) { return selector.split(",").some((s) => this.matches(s.trim())) ? this : this.parent?.closest(selector) || null; }
  querySelectorAll(selector) { const out = []; const walk = (n) => n.children.forEach((child) => { if (selector.split(",").some((s) => child.matches(s.trim()))) out.push(child); walk(child); }); walk(this); return out; }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((n) => n !== this); this.isConnected = false; }
  showModal() { this.open = true; Node.lastModal = this; this.querySelector("button")?.emit("click"); }
  close() { this.open = false; }
  prepend(...nodes) { const old = [...this.children]; this.replaceChildren(...nodes, ...old); }
  focus() {} scrollIntoView() {} scrollTo({ top = 0, left = 0 } = {}) { this.scrollTop = top; this.scrollLeft = left; } setPointerCapture() {} releasePointerCapture() {}
}

function makeHarness(regions) {
  const nodes = new Map();
  const $ = (id) => { if (!nodes.has(id)) { const node = new Node(); node.id = id; nodes.set(id, node); } return nodes.get(id); };
  const dialog = { mode: "view", page: 0, imageReady: true, saving: false, tool: "select", spacePan: false };
  const question = {
    id: 1, number: 1, group: { id: 10 }, question_type: "free_response",
    regions: regions.map((p) => r(p, [10, 10, 500, 500])),
    content_revision: 1, layout_fingerprint: "f".repeat(64), color_index: 1,
  };
  const state = {
    paperId: 7,
    paper: { id: 7, layout_revision: 9, pages, question_groups: [{ id: 10, sequence: 0, pages: [0, 1, 2], title: "组10" }] },
    questions: [question],
  };
  const panel = $("pageLayoutPanel");
  const host = {
    dialog, state, $, panel,
    el: (tag, cls, text) => new Node(tag, cls, text),
    button: (text, cls, fn, title) => {
      const n = new Node("button", cls, text);
      if (title) n.title = title;
      if (fn) n.addEventListener("click", fn);
      return n;
    },
    api: async () => ({ paper: state.paper, questions: state.questions, operations: [], layout_revision: 9 }),
    toast: () => {}, cropView: () => new Node(),
    renderStage: () => {},
    renderPageTabs() {},
    goToDialogPage: (page) => { dialog.page = page; },
    pointFrom: (e) => [e.clientX, e.clientY],
    placeBox: (node, bbox) => { node.bbox = [...bbox]; },
    setCropSaving: (saving) => { dialog.saving = saving; },
    trackCropDraft() {},
    confirmDialog: async () => true,
    TYPE_NAMES: { unknown: "未定", free_response: "解答题" },
    questionCompare: (x, y) => x.id - y.id,
    isApproved: () => false,
    reviewQuestion: () => {},
    applyCanonical: () => {},
    reloadCanonical: async () => {},
    returnReview: async () => {},
  };
  const controller = L.createController(host);
  return { host, controller, state, $, panel, question };
}

function findButtons(root, text) {
  return root.querySelectorAll("button").filter((b) => (b.textContent || "").trim() === text);
}

async function main() {
  global.window = new Node();
  global.crypto ||= require("node:crypto").webcrypto;
  global.document = { querySelector: () => null, body: new Node("body") };
  global.requestAnimationFrame = (callback) => callback();

  // ---- 只有一段：前移和后移都该灰，而且都得说清为什么 --------------------
  {
    const { controller, panel, question } = makeHarness([0]);
    controller.open("regions", question);
    const ups = findButtons(panel, "前移");
    const downs = findButtons(panel, "后移");
    assert.ok(ups.length >= 1, "面板里得有「前移」按钮");
    assert.ok(downs.length >= 1, "面板里得有「后移」按钮");
    assert.equal(ups[0].disabled, true, "只有一段时「前移」是灰的");
    assert.equal(downs[0].disabled, true, "只有一段时「后移」是灰的");
    assert.ok(ups[0].title && ups[0].title.length > 5, "灰掉的「前移」必须有悬停说明");
    assert.ok(downs[0].title && downs[0].title.length > 5, "灰掉的「后移」必须有悬停说明");
    assert.match(ups[0].title, /一段/, "灰掉的「前移」要说清是只有一段");
    assert.match(downs[0].title, /一段/, "灰掉的「后移」要说清是只有一段");
    assert.match(ups[0].title, /再加一块|能|才/, "灰掉的「前移」要说下一步能做什么");

    // 移除也要有话说
    const removes = findButtons(panel, "移除");
    assert.ok(removes.length >= 1, "面板里得有「移除」按钮");
    assert.ok(removes[0].title && removes[0].title.length > 5, "「移除」必须有悬停说明");
    assert.match(removes[0].title, /删除这题|不剩/, "只剩一段时「移除」要说清整题删掉走哪条路");
  }

  // ---- 三段：第一段前移灰、最后一段后移灰，中间都能用 --------------------
  {
    const { controller, panel, question } = makeHarness([0, 1, 2]);
    controller.open("regions", question);
    const ups = findButtons(panel, "前移");
    const downs = findButtons(panel, "后移");
    assert.equal(ups.length, 3, "三段就有三个「前移」");
    assert.equal(downs.length, 3, "三段就有三个「后移」");
    assert.equal(ups[0].disabled, true, "第一段的前移是灰的");
    assert.equal(downs[0].disabled, false, "第一段的后移是可用的");
    assert.equal(ups[2].disabled, false, "最后一段的前移是可用的");
    assert.equal(downs[2].disabled, true, "最后一段的后移是灰的");
    // 关键：灰的那两个必须说话，可用的也必须有话
    assert.ok(ups[0].title && ups[0].title.length > 5, "第一段灰掉的「前移」必须有话");
    assert.ok(downs[2].title && downs[2].title.length > 5, "最后一段灰掉的「后移」必须有话");
    assert.match(ups[0].title, /第一段/, "第一段的前移要说清是第一段");
    assert.match(downs[2].title, /最后一段/, "最后一段的后移要说清是最后一段");
    assert.ok(ups[1].title && ups[1].title.length > 5, "可用的「前移」也要有话");
    assert.ok(downs[1].title && downs[1].title.length > 5, "可用的「后移」也要有话");
    // 可用的按钮不许说「不能」
    assert.doesNotMatch(ups[1].title, /不能|不可以/, "可用的「前移」不许说『不能』");
    assert.doesNotMatch(downs[1].title, /不能|不可以/, "可用的「后移」不许说『不能』");
  }

  // ---- 撤销 / 重做：灰的时候要说清楚 ---------------------------------------
  {
    const { controller, $, question } = makeHarness([0, 1]);
    controller.open("regions", question);
    const undo = $("pageUndo");
    const redo = $("pageRedo");
    assert.equal(undo.disabled, true, "还没改东西时「撤销」是灰的");
    assert.equal(redo.disabled, true, "还没改东西时「重做」是灰的");
    assert.ok(undo.title && undo.title.length > 5, "灰掉的「撤销」必须有悬停说明");
    assert.ok(redo.title && redo.title.length > 5, "灰掉的「重做」必须有悬停说明");
    assert.match(undo.title, /还没有|先改/, "灰掉的「撤销」要说清现在没东西可撤");
    assert.match(redo.title, /还没有|先改/, "灰掉的「重做」要说清现在没东西可重做");
    assert.notEqual(undo.title, redo.title, "撤销和重做要说不同的话");
  }

  // ---- 调整范围面板里那两处刷新：都要接上同一套话术 ----------------------
  // 面板打开时走 configure()，改题号输入框时又刷一次，两处都得挂上话术。
  {
    const source = fs.readFileSync(path.join(__dirname, "original-paper-layout.js"), "utf8");
    const count = (needle) => source.split(needle).length - 1;
    assert.ok(count('.title = historyHint("undo"') >= 2, "撤销按钮的两处刷新都要接上共用话术");
    assert.ok(count('.title = historyHint("redo"') >= 2, "重做按钮的两处刷新都要接上共用话术");
    assert.ok(count(".title = partOrderHint(") >= 1, "片段顺序按钮必须接上共用话术");
    assert.ok(count(".title = partRemoveHint(") >= 1, "片段移除按钮必须接上共用话术");
  }

  // ---- 切题那套片段列表（在 app.js 里）：按钮必须接上同一套话术 ----------
  // app.js 那一整套没法在 Node 里起（它是整页应用），所以这里做接线核对：
  // 片段列表里的前移/后移/移除、以及撤销/重做，都必须真的从共用的话术里取。
  {
    const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
    const count = (needle) => source.split(needle).length - 1;
    assert.ok(count(".title = window.QBOriginalLayout.partOrderHint(") >= 2, "切题列表的前移和后移都必须接上共用话术");
    assert.ok(count(".title = window.QBOriginalLayout.partRemoveHint(") >= 1, "切题列表的移除必须接上共用话术");
    assert.ok(count(".title = window.QBOriginalLayout.historyHint(") >= 2, "切题列表的撤销和重做都必须接上共用话术");
  }

  console.log("Grey buttons carry real advice on the real panel: OK");
}

main().catch((error) => { console.error(error); process.exit(1); });
