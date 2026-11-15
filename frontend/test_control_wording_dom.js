"use strict";

// 1.26.39：光看话术表不算数 —— 得看真控件上是不是真挂上了那句话。
// 这里把源文件里那几段原样抠出来，在假 DOM 里真跑一遍，读真实的 textContent / title。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const W = require("./control-wording.js");
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
