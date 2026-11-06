"use strict";
// 1.26.38：DOM 级验证 —— 重新切题预演那颗灰按钮，**按钮上真的挂了话术**。
//
// 上一轮最要紧的一条教训：光验话术函数等于没验。函数写得再对，
// 按钮忘了把那句话挂上去，测试照样绿，而老师看到的还是那个不吭声的控件。
// 所以这个测试不读源码，而是真的把 app.js 里那段逻辑跑起来，
// 再读按钮节点上的 title 属性和旁边那行可见提示的文本。
//
// 跑法：把 app.js 里「重新切题预演」那一段原样取出来，放进一个函数里执行，
// 喂给它一套真 DOM 桩 —— 跟 test_grey_button_dom.js 用的是同一套路子。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const appSource = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const W = require("./resegment-preview-wording.js");

// ---- 一套够用的 DOM 桩 ------------------------------------------------
class Node {
  constructor(tag = "div", cls = "", text = "") {
    this.tagName = tag.toUpperCase();
    this.className = cls;
    this.textContent = text;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.isConnected = true;
    this.title = "";
    this.style = { setProperty: (key, value) => { this.style[key] = value; } };
    this.classList = {
      add: (...xs) => xs.forEach((x) => this.classList.toggle(x, true)),
      remove: (...xs) => xs.forEach((x) => this.classList.toggle(x, false)),
      contains: (x) => this.className.split(" ").includes(x),
      toggle: (x, value) => {
        const s = new Set(this.className.split(" ").filter(Boolean));
        const yes = value === undefined ? !s.has(x) : value;
        if (yes) s.add(x); else s.delete(x);
        this.className = [...s].join(" ");
        return yes;
      },
    };
  }
  append(...nodes) {
    nodes.forEach((n) => {
      if (typeof n === "string") n = new Node("text", "", n);
      n.parent = this;
      this.children.push(n);
    });
  }
  replaceChildren(...nodes) {
    this.children.forEach((n) => { n.parent = null; });
    this.children = [];
    this.append(...nodes);
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  async emit(type, event = {}) {
    const e = { target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...event };
    for (const fn of [...(this.listeners.get(type) || [])]) await fn(e);
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return selector.slice(1).split(".").every((x) => this.classList.contains(x));
    const data = selector.match(/^\[data-([a-z-]+)(?:="([^"]*)")?\]$/);
    if (data) {
      const key = data[1].replace(/-([a-z])/g, (_, x) => x.toUpperCase());
      return key in this.dataset && (data[2] === undefined || String(this.dataset[key]) === data[2]);
    }
    return this.tagName === selector.toUpperCase();
  }
  closest(selector) {
    return selector.split(",").some((s) => this.matches(s.trim())) ? this : this.parent?.closest(selector) || null;
  }
  querySelectorAll(selector) {
    const out = [];
    const walk = (n) => n.children.forEach((child) => {
      if (selector.split(",").some((s) => child.matches(s.trim()))) out.push(child);
      walk(child);
    });
    walk(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

// ---- 把 app.js 里那几段原样抠出来 -------------------------------------
// 只抠跟「这颗按钮」直接相关的：状态、刷话术、打开预演、渲染预演、预演失败。
// 抠出来原样执行，不改一个字 —— 改了就不是在验真正跑的那段代码了。
function extract(source, startMarker, endMarker) {
  const from = source.indexOf(startMarker);
  if (from < 0) throw new Error("抠不出：" + startMarker);
  const to = source.indexOf(endMarker, from);
  if (to < 0) throw new Error("抠不出结尾：" + endMarker);
  return source.slice(from, to);
}

const stateBlock = extract(appSource,
  "  const resegmentPreview = {",
  "  function setResegmentClosingDisabled");

const syncBlock = extract(appSource,
  "  function syncResegmentApply() {",
  "  function resetResegmentPreview");

const renderBlock = extract(appSource,
  "  function renderResegmentPreview(report) {",
  "  function showResegmentBlocked");

const blockedBlock = extract(appSource,
  "  function showResegmentBlocked(error) {",
  "  async function resegmentPaper");

const resetBlock = extract(appSource,
  "  function resetResegmentPreview(paperId) {",
  "  function resegmentSummaryCard");

// 勾确认框那个监听器：按钮什么时候能点，全在这儿
const acknowledgeBlock = extract(appSource,
  "  $(\"resegmentAcknowledge\").addEventListener(\"change\"",
  "  $(\"resegmentApply\").addEventListener(\"click\"");

// ---- 搭一套真 DOM，把那几段放进去跑 -----------------------------------
function makeDom() {
  const nodes = new Map();
  const $ = (id) => {
    if (!nodes.has(id)) { const n = new Node(); n.id = id; nodes.set(id, n); }
    return nodes.get(id);
  };
  // 弹窗里的「关闭」「取消」两颗
  const dialog = $("resegmentPreviewDialog");
  for (const label of ["关闭", "取消"]) {
    const close = new Node("button", "button", label);
    close.setAttribute("data-close", "");
    dialog.append(close);
  }
  return { $, dialog };
}

function build(dom, deps) {
  const { $, dialog } = dom;
  const body = `
    ${stateBlock}
    function setResegmentClosingDisabled(disabled) {
      $("resegmentPreviewDialog").querySelectorAll("[data-close]").forEach((button) => { button.disabled = disabled; });
    }
    ${syncBlock}
    ${resetBlock}
    function resegmentSummaryCard(category) {
      const card = new Node("div", "resegment-summary-card " + category.tone);
      card.append(new Node("strong", "", String(category.count)), new Node("span", "", category.label), new Node("small", "", category.detail));
      return card;
    }
    function resegmentItemRow(item) {
      const row = new Node("li");
      row.append(new Node("strong", "", deps.QBResegment.itemTitle(item)));
      if (item?.reason) row.append(new Node("span", "", String(item.reason)));
      return row;
    }
    ${renderBlock}
    ${blockedBlock}
    ${acknowledgeBlock}
    return { syncResegmentApply, resetResegmentPreview, renderResegmentPreview, showResegmentBlocked, resegmentPreview };
  `;
  const factory = new Function("QBResegment", "QBResegmentWording", "$", "dialog", "el", "api", "toast", "Node", "document", body);
  return factory(deps.QBResegment, deps.QBResegmentWording, $, dialog, deps.el, deps.api, deps.toast, Node, fakeDocument());
}

const QBResegment = {
  CATEGORIES: [
    { key: "kept", label: "原样保留", detail: "来源和范围不变", tone: "safe" },
    { key: "added", label: "新增题卡", detail: "新规则新找到的题卡", tone: "change" },
    { key: "locally_trimmed", label: "例题去解", detail: "只去除分析尾部", tone: "safe" },
    { key: "range_changed", label: "范围变化", detail: "撤销旧审批并重新识读", tone: "change" },
    { key: "suspected_excluded", label: "系统移入回收站", detail: "新规则未再命中", tone: "danger" },
    { key: "protected_unmatched", label: "受保护", detail: "你已通过或入库过", tone: "warning" },
    { key: "too_long", label: "异常超长", detail: "范围触及安全上限", tone: "warning" },
  ],
  normalizeReport(value) {
    const report = value && typeof value === "object" ? value : {};
    const summary = report.summary && typeof report.summary === "object" ? report.summary : {};
    const items = report.items && typeof report.items === "object" ? report.items : {};
    return {
      readOnly: report.read_only === true,
      modelCalls: Number.isFinite(Number(report.model_calls)) ? Number(report.model_calls) : null,
      categories: QBResegment.CATEGORIES.map((category) => ({
        ...category,
        count: Math.max(0, Number.parseInt(summary[category.key], 10) || 0),
        items: Array.isArray(items[category.key]) ? items[category.key] : [],
      })),
      notes: Array.isArray(report.notes) ? report.notes.map(String) : [],
    };
  },
  itemTitle(item) {
    const group = String(item?.group || "").trim();
    const number = item?.number === null || item?.number === undefined ? "题号未定" : `第 ${item.number} 题`;
    const pages = Array.isArray(item?.pages) ? item.pages.filter((p) => Number.isFinite(Number(p))).map(Number) : [];
    const pageText = pages.length ? ` · 第 ${pages.join("、")} 页` : "";
    return `${group ? `${group} · ` : ""}${number}${pageText}`;
  },
};

// 最小 document 桩：那一段里只用到 createTextNode
function fakeDocument() {
  return { createTextNode: (text) => new Node("text", "", String(text)) };
}

function scenario() {
  const dom = makeDom();
  const deps = {
    QBResegment,
    QBResegmentWording: W,
    el: (tag, cls, text) => new Node(tag, cls, text),
    api: async () => ({}),
    toast: () => {},
  };
  const built = build(dom, deps);
  return { dom, built, $: dom.$ };
}

async function main() {
  // ---- 真跑一遍，看按钮上到底挂了什么 ---------------------------------

  // 1) 预演还在算的时候
  {
    const { built, $ } = scenario();
    built.resetResegmentPreview("paper-1");
    const apply = $("resegmentApply");
    const visible = $("resegmentApplyHint");
    assert.equal(apply.disabled, true, "预演在算的时候按钮是灰的");
    assert.ok(apply.title && apply.title.length > 0, "按钮上必须挂上悬停说明");
    assert.ok(apply.title.includes("预演"), "悬停说明要说清在算什么，实际是「" + apply.title + "」");
    assert.ok(visible.textContent && visible.textContent.length > 0, "旁边那行可见提示必须有字");
    assert.equal(visible.hidden, false, "有话要说的时候这行字不能是藏着的");
  }

  // 2) 预演出来了，但还没勾确认框
  {
    const { built, $ } = scenario();
    built.resetResegmentPreview("paper-1");
    built.renderResegmentPreview({ read_only: true, summary: { kept: 3 }, items: {} });
    const apply = $("resegmentApply");
    const visible = $("resegmentApplyHint");
    assert.equal(apply.disabled, true, "还没勾确认框时按钮是灰的");
    assert.ok(apply.title.includes("确认框"), "悬停说明要说清下一步是勾确认框，实际是「" + apply.title + "」");
    assert.ok(visible.textContent.includes("确认框"), "旁边那行字也要说清下一步，实际是「" + visible.textContent + "」");
  }

  // 3) 勾了确认框 —— 按钮能点了，这行字就该收起来
  {
    const { built, $ } = scenario();
    built.resetResegmentPreview("paper-1");
    built.renderResegmentPreview({ read_only: true, summary: { kept: 3 }, items: {} });
    // 真去勾那个确认框，不是直接改内部状态 —— 走的正是老师点的那条路
    const box = $("resegmentAcknowledge");
    box.checked = true;
    await box.emit("change");
    const apply = $("resegmentApply");
    const visible = $("resegmentApplyHint");
    assert.equal(apply.disabled, false, "勾了确认框以后按钮该能点");
    assert.ok(apply.title.includes("应用"), "能点的时候也要说清这一步做什么，实际是「" + apply.title + "」");
    assert.equal(visible.textContent, "", "能点的时候旁边那行提示要收起来");
    assert.equal(visible.hidden, true, "能点的时候旁边那行提示要藏起来");
  }

  // 4) 预演没跑成
  {
    const { built, $ } = scenario();
    built.resetResegmentPreview("paper-1");
    built.showResegmentBlocked(new Error("这份资料还没处理完"));
    const apply = $("resegmentApply");
    const visible = $("resegmentApplyHint");
    assert.equal(apply.disabled, true, "预演没跑成功时按钮是灰的");
    assert.ok(apply.title.includes("原因"), "悬停说明要指向原因，实际是「" + apply.title + "」");
    assert.ok(visible.textContent.length > 0, "旁边那行可见提示也要有字");
  }

  // 5) 服务端不认这次预演
  {
    const { built, $ } = scenario();
    built.resetResegmentPreview("paper-1");
    built.renderResegmentPreview({ read_only: false, summary: {}, items: {} });
    const apply = $("resegmentApply");
    const visible = $("resegmentApplyHint");
    assert.equal(apply.disabled, true, "服务端不认时按钮是灰的");
    assert.ok(apply.title.includes("只读"), "悬停说明要说清是只读预演没被确认，实际是「" + apply.title + "」");
    assert.ok(visible.textContent.length > 0, "旁边那行可见提示也要有字");
  }

  // ---- 每种状态下，那行可见提示都不能是空的 ----------------------------
  {
    const states = [
      () => { const s = scenario(); s.built.resetResegmentPreview("p"); return s; },
      () => { const s = scenario(); s.built.resetResegmentPreview("p"); s.built.renderResegmentPreview({ read_only: true, summary: {}, items: {} }); return s; },
      () => { const s = scenario(); s.built.resetResegmentPreview("p"); s.built.showResegmentBlocked(new Error("x")); return s; },
      () => { const s = scenario(); s.built.resetResegmentPreview("p"); s.built.renderResegmentPreview({ read_only: false, summary: {}, items: {} }); return s; },
    ];
    for (const make of states) {
      const { $ } = make();
      const apply = $("resegmentApply");
      assert.equal(apply.disabled, true, "这几种情况下按钮都该是灰的");
      assert.ok((apply.title || "").length > 0, "灰着的时候按钮上必须有话");
      const visible = $("resegmentApplyHint");
      assert.ok((visible.textContent || "").length > 0, "灰着的时候旁边那行可见提示必须有字");
    }
  }

}

main().catch((error) => { console.error(error); process.exit(1); });
console.log("resegment apply grey button (DOM level): OK");
