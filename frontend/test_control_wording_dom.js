"use strict";

// 1.26.39：光看话术表不算数 —— 得看真控件上是不是真挂上了那句话。
// 这里把源文件里那几段原样抠出来，在假 DOM 里真跑一遍，读真实的 textContent / title。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const W = require("./control-wording.js");
const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

// ── 一套够用的假 DOM ────────────────────────────────────────
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attrs = {};
    this.className = "";
    this.title = "";
    this._text = "";
  }
  append(...kids) {
    for (const kid of kids) {
      this.children.push(kid);
      if (kid && typeof kid === "object") kid.parent = this;
    }
  }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name]; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  get textContent() {
    return this._text + this.children.map((kid) => (typeof kid === "string" ? kid : (kid.textContent || ""))).join("");
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  querySelectorAll(selector) {
    const tags = selector.split(",").map((value) => value.trim().toUpperCase());
    const found = [];
    const walk = (element) => {
      for (const kid of element.children) {
        // 文本节点没有 children，元素才有。
        if (!kid || typeof kid !== "object" || !kid.children) continue;
        if (tags.includes(kid.tagName)) found.push(kid);
        walk(kid);
      }
    };
    walk(this);
    return found;
  }
}

const document = {
  createElement: (tag) => new El(tag),
  createElementNS: (_ns, tag) => new El(tag),
  createTextNode: (value) => ({ textContent: String(value) }),
};

// ── 从源文件里原样抠出一段 ──────────────────────────────────
function sliceFunction(source, name) {
  const start = source.indexOf(`\n  function ${name}(`);
  assert.notEqual(start, -1, `源文件里找不到 ${name}，剪贴代码的人要改`);
  const parenStart = source.indexOf("(", start);
  let depth = 0;
  let i = parenStart;
  for (; i < source.length; i += 1) {
    if (source[i] === "(") depth += 1;
    else if (source[i] === ")") { depth -= 1; if (!depth) break; }
  }
  const bodyStart = source.indexOf("{", i);
  depth = 0;
  for (i = bodyStart; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") { depth -= 1; if (!depth) break; }
  }
  return source.slice(start + 1, i + 1);
}

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `源文件里找不到起点：${startMarker}`);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, `源文件里找不到终点：${endMarker}`);
  return source.slice(start, end + endMarker.length);
}

// ── 一、顶栏那颗开关：开着、收着、灰着，说法都要变 ──────────
{
  const code = sliceBetween(libraryJs, "  function markInspectorToggle(", "\n\n");
  const toggle = { attrs: {}, title: "", textContent: "", setAttribute(n, v) { this.attrs[n] = String(v); }, getAttribute(n) { return this.attrs[n]; } };
  const sandbox = { ui: { inspectorToggle: toggle }, W };
  vm.runInNewContext(code, sandbox);

  sandbox.markInspectorToggle(true);
  assert.equal(toggle.textContent, W.INSPECTOR.expanded.label, "面板开着时，按钮要写「收起详情」");
  assert.equal(toggle.title, W.INSPECTOR.expanded.hint, "面板开着时，悬停说明要说清怎么再打开");
  assert.equal(toggle.getAttribute("aria-label"), W.INSPECTOR.expanded.label);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");

  sandbox.markInspectorToggle(false);
  assert.equal(toggle.textContent, W.INSPECTOR.collapsed.label, "面板收着时，按钮要写「展开详情」");
  assert.equal(toggle.title, W.INSPECTOR.collapsed.hint);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");

  sandbox.markInspectorToggle(true, { disabled: true });
  assert.equal(toggle.textContent, W.INSPECTOR.disabled.label,
    "灰掉时「为什么灰、下一步去哪」要写在按钮上 —— 禁用时悬停气泡根本不弹");
  assert.match(toggle.title, /先在左边点一道题/);
  assert.equal(toggle.getAttribute("aria-expanded"), "false", "灰着的开关不算开着");
}

// ── 二、面板里那颗「收起详情」 ──────────────────────────────
{
  const code = sliceBetween(libraryJs,
    '  const closeInspectorButton = $("closeInspector");',
    "    closeInspectorButton.title = W.INSPECTOR.expanded.hint;\n  }");
  const button = new El("button");
  const sandbox = { $: (id) => (id === "closeInspector" ? button : null), W };
  vm.runInNewContext(code, sandbox);
  assert.equal(button.textContent, W.INSPECTOR.expanded.label, "面板里那颗按钮的字要跟话术表一样");
  assert.equal(button.title, W.INSPECTOR.expanded.hint, "面板里那颗按钮的悬停说明要跟话术表一样");
}

// ── 三、教材目录面板那颗「收起目录」 ──────────────────────────
{
  const code = sliceBetween(libraryJs,
    '    const close = node("button", "button button-small button-quiet", W.DIRECTORY.label);',
    "    close.title = W.DIRECTORY.hint;");
  const sandbox = {
    W,
    node: (tag, className, text) => { const element = new El(tag); element.className = className; if (text !== undefined) element.textContent = text; return element; },
  };
  vm.runInNewContext(`${code}\nglobalThis.__out = close;`, sandbox);
  assert.equal(sandbox.__out.textContent, W.DIRECTORY.label, "教材目录那颗按钮的字要跟话术表一样");
  assert.equal(sandbox.__out.title, W.DIRECTORY.hint, "教材目录那颗按钮要说清再从哪儿打开");
}

// ── 四、题卡上的「更多」：说明按菜单里实际有的项生成 ────────
function runLibraryMenu(jobButtons) {
  const helpers = ["node", "icon", "iconButton"].map((name) => sliceFunction(libraryJs, name)).join("\n");
  const block = sliceBetween(libraryJs,
    '    const more = node("details", "library-card-more");',
    "    moreSummary.title = W.menuSummaryTitle(Array.from(menu.querySelectorAll(\"button, a\")).map((entry) => entry.textContent.trim()));");
  const sandbox = {
    document, W,
    item: { id: "q-1", document_id: "doc-1" },
    state: { basket: [] },
    jobButtons,
    draftLink: () => "/?document=doc-1",
    // 事件回调里才用到，跑构造过程碰不到；列出来是为了别在报错时看不懂。
    selectInspectorQuestion() {}, openSource() {}, openQuestionViewer() {},
    openAnswerEditor() {}, openQuestionEditor() {}, withdrawItem() {},
    saveBasket() {}, render() {}, toast() {},
    copyQuestionImage() {},
  };
  vm.runInNewContext(`${helpers}\n${block}\nglobalThis.__out = { moreSummary, menu };`, sandbox);
  return sandbox.__out;
}

{
  const { moreSummary, menu } = runLibraryMenu(() => []);
  const labels = menu.querySelectorAll("button, a").map((entry) => entry.textContent);
  assert.deepEqual(labels, ["修改题目", "编辑答案解析", "回到题卡", "复制题目图片", "撤回"],
    "题卡菜单里实际就是这几项");
  assert.equal(moreSummary.title, W.menuSummaryTitle(labels),
    "「更多」的悬停说明要跟菜单里实际有的项对得上");
  assert.match(moreSummary.title, /修改题目/);
  assert.doesNotMatch(moreSummary.title, /生成标签建议/, "菜单里没有的项不许出现在说明里");
}

{
  const extra = new El("button");
  extra.textContent = "AI 生成标签建议";
  const { moreSummary } = runLibraryMenu(() => [extra]);
  assert.match(moreSummary.title, /AI 生成标签建议/, "菜单里多出来的项也要写进说明");
}

// ── 五、核对页题卡上的「更多」 ──────────────────────────────
function runAppMenu(question) {
  const helpers = ["el", "icon", "button"].map((name) => sliceFunction(appJs, name)).join("\n");
  const block = sliceBetween(appJs,
    '    const more = el("details", "more");',
    "    summary.title = QBControlWording.menuSummaryTitle(Array.from(menu.querySelectorAll(\"button, a\")).map((entry) => entry.textContent.trim()));");
  const sandbox = {
    document, QBControlWording: W, q: question,
    openOriginEditor() {}, rereadQuestion() {}, deleteQuestion() {},
  };
  vm.runInNewContext(`${helpers}\n${block}\nglobalThis.__out = { summary, menu };`, sandbox);
  return sandbox.__out;
}

{
  const { summary, menu } = runAppMenu({ body_mode: "text" });
  const labels = menu.querySelectorAll("button, a").map((entry) => entry.textContent);
  assert.deepEqual(labels, ["修改题目来源", "让 AI 重读这题", "删除这张卡"], "核对页菜单里实际就是这几项");
  assert.equal(summary.title, W.menuSummaryTitle(labels), "核对页「更多」的说明要跟菜单对得上");
}

{
  const { summary, menu } = runAppMenu({ body_mode: "source_image" });
  const labels = menu.querySelectorAll("button, a").map((entry) => entry.textContent);
  assert.deepEqual(labels, ["修改题目来源", "删除这张卡"], "照片题没有「让 AI 重读这题」");
  assert.doesNotMatch(summary.title, /重读/, "菜单里没有的项不许出现在说明里");
}

console.log("control wording dom: ok");
