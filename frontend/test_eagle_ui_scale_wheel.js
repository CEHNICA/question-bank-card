"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function extractUiScaleScript() {
  const start = eagleHtml.indexOf("    const EAGLE_UI_SCALE_KEY = 'eagle_ui_scale_v1';");
  const end = eagleHtml.indexOf("    function readRecentQuestions()", start);
  assert.ok(start > 0 && end > start, "应能定位到 Eagle 界面缩放脚本段");
  return eagleHtml.slice(start, end);
}

function createWheelHarness() {
  const listeners = new Map();
  const storage = new Map();
  const toasts = [];
  const docEl = { style: { zoom: "" }, dataset: {} };
  const elements = {
    eagleUiScaleMenuLabel: { textContent: "100%" },
    eagleUiScaleRange: { value: "100" },
  };
  const context = {
    Math,
    Number,
    String,
    setTimeout,
    clearTimeout,
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
    document: {
      documentElement: docEl,
      getElementById: (id) => elements[id] || null,
    },
    window: {
      innerHeight: 900,
      addEventListener: (type, fn, opts) => {
        listeners.set(type, { fn, opts });
      },
    },
    showToast: (msg, kind) => toasts.push({ msg, kind }),
  };

  vm.runInNewContext(extractUiScaleScript(), context);
  const wheelEntry = listeners.get("wheel");
  assert.ok(wheelEntry, "应注册 window wheel 监听");
  assert.equal(wheelEntry.opts && wheelEntry.opts.passive, false, "wheel 监听必须设 passive: false 以阻止浏览器默认缩放");

  function emitWheel(overrides = {}) {
    let prevented = Boolean(overrides.defaultPrevented);
    const event = {
      ctrlKey: false,
      altKey: false,
      metaKey: false,
      shiftKey: false,
      deltaY: 0,
      deltaMode: 0,
      target: { closest: () => null },
      get defaultPrevented() {
        return prevented;
      },
      preventDefault: () => {
        prevented = true;
      },
      ...overrides,
    };
    wheelEntry.fn(event);
    return { prevented };
  }

  return { docEl, elements, storage, toasts, emitWheel, context };
}

test("Eagle 工作台：顶栏移除 A−/A+ 按钮并支持 Ctrl + 滚轮缩放整页字体与界面", () => {
  assert.ok(!eagleHtml.includes('id="eagleUiScaleBadge"'), "顶栏不应再显示 A−/100%/A+ 按钮组");
  assert.ok(!eagleHtml.includes(">A−</button>"), "顶栏不应再保留 A− 按钮");
  assert.ok(!eagleHtml.includes(">A+</button>"), "顶栏不应再保留 A+ 按钮");
  assert.ok(eagleHtml.includes("Ctrl + 滚轮"), "快捷键指南应注明 Ctrl + 滚轮");

  const h = createWheelHarness();
  assert.equal(h.docEl.style.zoom, "1");

  // 1. 普通滚轮不拦截、不缩放
  const plain = h.emitWheel({ ctrlKey: false, deltaY: -120 });
  assert.equal(plain.prevented, false);
  assert.equal(h.docEl.style.zoom, "1");

  // 2. Ctrl + 向上滚轮放大界面
  const zoomIn = h.emitWheel({ ctrlKey: true, deltaY: -120 });
  assert.equal(zoomIn.prevented, true);
  assert.equal(h.docEl.style.zoom, "1.05");
  assert.equal(h.elements.eagleUiScaleMenuLabel.textContent, "105%");
  assert.equal(h.storage.get("eagle_ui_scale_v1"), "1.05");
  assert.equal(h.toasts.at(-1).msg, "界面大小 105%");

  // 3. Ctrl + 向下滚轮缩小界面
  const zoomOut = h.emitWheel({ ctrlKey: true, deltaY: 120 });
  assert.equal(zoomOut.prevented, true);
  assert.equal(h.docEl.style.zoom, "1");
  assert.equal(h.elements.eagleUiScaleMenuLabel.textContent, "100%");

  // 4. 在原卷对照视口 #opViewport 内不劫持 Ctrl+滚轮
  const inOp = h.emitWheel({
    ctrlKey: true,
    deltaY: -120,
    target: { closest: (sel) => (sel === "#opViewport" ? {} : null) },
  });
  assert.equal(inOp.prevented, false);
  assert.equal(h.docEl.style.zoom, "1");

  // 5. 微小触控板滚动需累积达阈值后才步进，且反向即清零
  h.emitWheel({ ctrlKey: true, deltaY: -20 });
  assert.equal(h.docEl.style.zoom, "1", "单次微小增量不应直接跳档");
  h.emitWheel({ ctrlKey: true, deltaY: 25 });
  assert.equal(h.docEl.style.zoom, "1", "反向滚动应重置累积并等待达到阈值");
  h.emitWheel({ ctrlKey: true, deltaY: 25 });
  assert.equal(h.docEl.style.zoom, "0.95", "同向累积达到阈值后应步进一档");
});
