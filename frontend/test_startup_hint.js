"use strict";
// 1.26.50：软件打开后，试卷列表还在准备的那段时间里，
// 左边「试卷」那一栏不能是一片空白 —— 要说清楚现在是什么状态。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const SOURCE = fs.readFileSync(path.join(__dirname, "startup-hint.js"), "utf8");

function makeElement(tag) {
  const element = {
    tagName: tag.toUpperCase(),
    className: "",
    textContent: "",
    children: [],
    attributes: {},
    connected: true,
    parent: null,
    get isConnected() { return this.connected; },
    append(...items) {
      this.children.push(...items);
      items.forEach((item) => { item.parent = this; });
    },
    // 真的从父节点里摘掉 —— 原来只是把 children 清空，
    // 于是「提示有没有被收走」这件事根本没被验到。
    remove() {
      this.connected = false;
      if (this.parent) {
        this.parent.children = this.parent.children.filter((item) => item !== this);
        this.parent = null;
      }
      this.children = [];
    },
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  return element;
}

function run({ readyState = "complete" } = {}) {
  const list = makeElement("ol");
  const timers = [];
  const observers = [];
  const document = {
    readyState,
    body: makeElement("body"),
    createElement: (tag) => makeElement(tag),
    getElementById: (id) => (id === "paperList" ? list : null),
    addEventListener: () => {},
  };
  const context = {
    document,
    window: {
      setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; },
    },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe() {}
      disconnect() { this.disconnected = true; }
    },
  };
  context.window.document = document;
  vm.createContext(context);
  vm.runInContext(SOURCE, context);

  const fire = (delay) => {
    timers.filter((item) => item.delay === delay).forEach((item) => item.fn());
  };
  return { list, timers, observers, fire, document };
}

test("列表还没好时，那一栏里说清楚在准备什么", () => {
  const { list, fire } = run();
  fire(1200);
  const hint = list.children.find((item) => item.className.includes("startup-hint"));
  assert(hint, "列表空着的时候要有提示");
  assert(hint.textContent.includes("正在准备"), "提示要说在做什么");
  assert(hint.textContent.includes("试卷列表"), "提示要说清是哪一个列表");
  assert.equal(hint.getAttribute, undefined, "提示是普通元素");
});

test("列表一出来，提示就收走", () => {
  const { list, fire, observers } = run();
  fire(1200);
  assert(list.children.some((item) => item.className.includes("startup-hint")));

  // 真正的列表来了（app.js 会清空再填，这里用追加来模拟）
  list.append(makeElement("li"));
  observers[0].callback();

  assert(!list.children.some((item) => item.className.includes("startup-hint")), "列表好了就不能再挡着");
  assert.equal(observers[0].disconnected, true, "好了之后就不用再盯着了");
});

test("提示收走之后，列表里就再也看不到它了", () => {
  const { list, fire, observers } = run();
  fire(1200);
  const hint = list.children.find((item) => item.className.includes("startup-hint"));
  assert(hint, "先要有提示");

  list.append(makeElement("li"));
  observers[0].callback();

  assert(!hint.isConnected, "提示本身要被摘掉，不只是不再管它");
  assert.equal(
    list.children.filter((item) => item.className.includes("startup-hint")).length,
    0,
    "列表里不能还留着提示",
  );
});

test("热启动时列表马上就到，不闪提示", () => {
  const { list, fire } = run();
  // 列表在提示出现之前就到了
  list.append(makeElement("li"));
  fire(1200);
  assert(!list.children.some((item) => item.className.includes("startup-hint")), "快的时候不该闪一下");
});

test("等太久要说清程序会自己再试", () => {
  const { list, fire } = run();
  fire(1200);
  fire(60000);
  const hint = list.children.find((item) => item.className.includes("startup-hint"));
  assert(hint, "还在等就得有提示");
  assert(hint.textContent.includes("再试"), "要说清程序会自己重试，不是死在那儿");
  assert(!hint.textContent.includes("正在准备"), "等久了就不该还说正在准备");
});

test("提示写在列表那一栏里，不放到别处", () => {
  const { list, fire, document } = run();
  fire(1200);
  assert(list.children.length > 0, "提示要待在列表里");
  assert.equal(document.body.children.length, 0, "不要往页面别处塞东西");
});
