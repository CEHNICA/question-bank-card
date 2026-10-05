"use strict";

// 关闭 API 配置窗口曾经“卡一下”：模型设置没保存时，关闭会先等一次
// /api/settings/models，再等一次 loadStatus，两次往返串行，而且中间什么都不显示。
// 本机的后台任务正在写库时，这两次往返足够长，长到像是点了没反应。
//
// 实测（真实 Chromium、仓库自己的 styles.css 和 credentialDialog 标记，
// 20 次取中位数）：关闭那一帧的间隔只有 3 ms，去掉 ::backdrop 模糊没有区别，
// 把关闭逻辑延后一帧反而更慢。渲染不是原因，等待才是。所以这里钉的是
// “只等一次往返”和“等待期间必须看得见”。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const dom = require("./credential-test-dom.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const credentialCode = js.slice(js.indexOf("  const CREDENTIAL_FIELDS = {"),
  js.indexOf("  function renderSettingsTask("));
const modelCode = js.slice(js.indexOf("  let modelSaving = Promise.resolve();"),
  js.indexOf("  async function prepareSettingsLeave("))
  + "\n" + js.slice(js.indexOf("  async function saveModelSettingsNow("),
    js.indexOf("\n  }\n", js.indexOf("  async function saveModelSettingsNow(")) + 4);
assert.ok(credentialCode.length > 0 && modelCode.length > 0);
assert.ok(js.indexOf("  const CREDENTIAL_FIELDS = {") < js.indexOf("  let modelSaving"));

const providers = ["mineru", "modelscope", "minimax", "siliconflow"];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject }; };
const turns = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function scenario() {
  const nodes = new Map(), requests = [], messages = [], statusCalls = [], closeButtons = [], nativeTasks = [];
  const saved = Object.fromEntries(providers.map((p) => [p, { configured: true, count: 1 }]));
  function $(id) {
    if (!nodes.has(id)) nodes.set(id, { ...dom.node(id),
      parentElement: { append() {} },
      addEventListener(type, fn) { (this.events[type] ||= []).push(fn); },
      querySelectorAll() { return closeButtons; },
      showModal() { this.open = true; },
      close() { if (!this.open) return; this.open = false; nativeTasks.push(() => this.events.close?.forEach((fn) => fn({ target: this }))); },
      focus() {} });
    return nodes.get(id);
  }
  for (const label of ["close", "cancel"]) closeButtons.push({ label, disabled: false, events: {},
    addEventListener(type, fn) { (this.events[type] ||= []).push(fn); }, closest() { return $("credentialDialog"); } });
  const dialogs = [$("credentialDialog"), $("pageDialog")];
  const context = vm.createContext({ $, console, state: { status: null }, credentialSourceNote: () => "", el: dom.el, icon: dom.icon, AbortController, setTimeout, clearTimeout,
    window: { addEventListener() {} },
    button: (...args) => dom.el("button", "", ...args),
    requestAnimationFrame(fn) { fn(); return 1; }, cancelAnimationFrame() {},
    anyDialogOpen() { return dialogs.some((d) => d.open); },
    document: { addEventListener() {}, querySelectorAll: (s) => (s === "dialog [data-close]" ? closeButtons : dialogs) },
    toast(message, kind) { messages.push({ message, kind }); },
    confirmDialog: async () => true,
    loadStatus: async () => { statusCalls.push("loadStatus"); return true; },
    api: async (url, options) => { requests.push({ url, body: (options || {}).body }); return { services: saved }; }
  });
  vm.runInContext(credentialCode + modelCode
    + "\nglobalThis.dirty = (value) => { modelFormDirty = value; };"
    + "\nglobalThis.close = (options) => requestCredentialClose(options);", context);
  const drainNative = () => { while (nativeTasks.length) nativeTasks.shift()(); };
  return { context, $, requests, messages, statusCalls, closeButtons, drainNative };
}

(async () => {
  // 1. 关闭时不再等第二次往返。
  const one = scenario();
  await one.context.openCredentialSettings();
  one.requests.length = 0;
  one.statusCalls.length = 0;
  one.context.dirty(true);
  one.context.close();
  await turns();
  assert.equal(one.$("credentialDialog").open, false, "the dialog closes once the save is done");
  assert.deepEqual(one.requests.map((r) => r.url), ["/api/settings/models"],
    "closing waits for the save only; the status refresh is not a gate");
  assert.equal(one.statusCalls.length, 1, "the status is still refreshed, just not before the close");

  // 2. 等待期间看得见：不是“点了没反应”。
  const slow = scenario(), save = deferred(), status = deferred();
  await slow.context.openCredentialSettings();
  slow.context.dirty(true);
  slow.context.loadStatus = async () => { slow.statusCalls.push("slow"); return status.promise; };
  slow.context.api = async (url) => { slow.requests.push({ url }); return save.promise; };
  slow.context.close();
  await turns();
  assert.equal(slow.$("credentialDialog").open, true, "still open while the save is in flight");
  assert.match(slow.$("credentialResult").textContent, /正在保存后关闭/,
    "a close that has to wait must say so, or the window looks frozen");
  assert.ok(slow.closeButtons.every((button) => button.disabled),
    "the close buttons are disabled so one click cannot queue a second close");
  save.resolve({ services: {} });
  await turns();
  assert.equal(slow.$("credentialDialog").open, false);

  // 3. 关闭被拒绝时，按钮和提示都要复原——否则留下的正是那个“卡住”的窗口。
  const refused = scenario();
  await refused.context.openCredentialSettings();
  refused.context.dirty(true);
  refused.context.api = async (url) => { refused.requests.push({ url }); throw new Error("network down"); };
  refused.context.close();
  await turns();
  assert.equal(refused.$("credentialDialog").open, true, "a failed save must not close the window");
  assert.equal(refused.$("credentialResult").textContent, "",
    "the pending line is cleared so it cannot be mistaken for a live progress state");
  assert.ok(refused.closeButtons.every((button) => !button.disabled), "the close buttons work again");
  assert.ok(refused.messages.some((m) => m.kind === "error"), "the failure is reported");

  // 4. 什么都没改的时候，关闭不产生任何请求。
  const clean = scenario();
  await clean.context.openCredentialSettings();
  clean.requests.length = 0;
  clean.context.close();
  await turns();
  clean.drainNative();
  assert.equal(clean.$("credentialDialog").open, false);
  assert.deepEqual(clean.requests.map((r) => r.url), [],
    "closing an untouched dialog never talks to the server");

  console.log("credential close ok");
})();
