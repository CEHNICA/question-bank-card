"use strict";

// 统一弹窗关闭守卫与离开保护测试：
// - 加载中关闭不阻断、恢复焦点；
// - 正在保存/测试时禁止关闭；
// - 有未保存改动时先提示（保存并关闭 / 放弃改动）；
// - 无改动直接关闭并隐藏明文。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};

class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.id = "";
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.open = false;
    this.readOnly = false;
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
  }
  append(...items) {
    for (const i of items) if (i && typeof i === "object") { this.children.push(i); i.parentElement = this; }
  }
  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }
  addEventListener(name, fn) {
    const arr = this.listeners.get(name) || [];
    arr.push(fn);
    this.listeners.set(name, arr);
  }
  trigger(name, detail = {}) {
    for (const fn of this.listeners.get(name) || []) fn({ target: this, preventDefault() {}, ...detail });
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  showModal() { this.open = true; }
  close() { if (!this.open) return; this.open = false; this.trigger("close"); }
  focus() { this.focused = true; }
}

const sample = {
  catalog: {
    services: [
      { key: "mineru", label: "MinerU", roles: ["cutting"] },
      { key: "deepseek", label: "DeepSeek", roles: ["answering"], answer_defaults: { model: "deepseek-v4-pro", base_url: "https://api.deepseek.com", supports_images: false, thinking: true } },
    ],
    vision_order: [],
    answer_order: ["deepseek"],
  },
  max_accounts: 8,
  credentials: { mineru: { configured: true, count: 1 }, deepseek: { configured: false, count: 0 } },
  reading: { primary: "assistant", models: {}, plans: { minimax: "auto" }, readiness: { ready: true } },
  answering: { mode: "assistant", provider: "deepseek", features: { ai_answer: false, knowledge_tags: false }, on_intake: { answer: false } },
};

function setup(fetcher) {
  const nodes = new Map();
  const register = (n) => { if (n?.id) nodes.set(n.id, n); for (const c of n?.children || []) register(c); };
  const dlg = new El("dialog"); dlg.id = "credentialDialog";
  const body = new El("div"); body.id = "credentialDialogBody";
  const orig = body.replaceChildren.bind(body);
  body.replaceChildren = (...items) => { orig(...items); for (const i of items) register(i); };
  nodes.set("credentialDialog", dlg);
  nodes.set("credentialDialogBody", body);

  const opener = new El("button");
  let confirmAnswer = true;
  const confirmations = [];
  const toasts = [];
  const document = {
    readyState: "complete",
    hidden: false,
    activeElement: opener,
    createElement: (t) => new El(t),
    getElementById: (id) => nodes.get(id) || null,
    addEventListener() {},
    dispatchEvent() {},
  };
  const windowObj = { location: { pathname: "/", hash: "" }, setTimeout, clearTimeout, addEventListener() {} };
  const sandbox = {
    window: windowObj,
    document,
    HTMLElement: El,
    AbortController,
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    setTimeout,
    clearTimeout,
    fetch: fetcher,
  };
  vm.runInNewContext(apiJs, sandbox);
  const configureOptions = {
    confirm: async (opts) => { confirmations.push(opts); return confirmAnswer; },
    toast: (m, t) => toasts.push({ m, t }),
  };
  return {
    windowObj, nodes, dlg, opener, confirmations, toasts,
    setConfirm: (v) => { confirmAnswer = v; },
    open: () => windowObj.APISettings.open(configureOptions),
    mount: () => windowObj.APISettings.mount(body, { ...configureOptions, mode: "modal" }),
  };
}

(async () => {
  // 1. 加载过程中直接点关闭：立即关闭并将焦点交还给触发元素
  const getWait = deferred();
  const loading = setup(async () => getWait.promise);
  const openTask = loading.open();
  assert.equal(loading.dlg.open, true);
  const closedImmediately = await loading.windowObj.APISettings.close();
  assert.equal(closedImmediately, true);
  assert.equal(loading.dlg.open, false);
  assert.equal(loading.opener.focused, true, "returns focus to trigger button on close");
  getWait.resolve({ ok: true, status: 200, text: async () => JSON.stringify(sample) });
  await openTask;

  // 2. 保存进行中时禁止关闭窗口或离开设置页
  const postWait = deferred();
  const busy = setup(async (_url, opts = {}) => {
    if (opts.method === "POST") return postWait.promise;
    return { ok: true, status: 200, text: async () => JSON.stringify(sample) };
  });
  await busy.open();
  const saveTask = busy.windowObj.APISettings.save();
  assert.equal(busy.windowObj.APISettings.isMutating(), true);
  assert.equal(await busy.windowObj.APISettings.close(), false, "blocks close while save is in flight");
  assert.equal(await busy.windowObj.APISettings.prepareLeave(), false, "blocks navigation while save is in flight");
  postWait.resolve({ ok: true, status: 200, text: async () => JSON.stringify({ ...sample, message: "ok" }) });
  await saveTask;
  assert.equal(busy.windowObj.APISettings.isMutating(), false);

  // 3. 有未保存输入时：选择「放弃改动」可离开并清空草稿；选择「保存并关闭」则保存成功后关闭
  let saveCalls = 0;
  const dirty = setup(async (_url, opts = {}) => {
    if (opts.method === "POST") {
      saveCalls += 1;
      return { ok: true, status: 200, text: async () => JSON.stringify({ ...sample, message: "saved" }) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(sample) };
  });
  await dirty.open();
  const mineruIn = dirty.nodes.get("apiKeyInput-mineru");
  mineruIn.value = "draft-unsaved-tok";
  mineruIn.trigger("input");
  assert.equal(dirty.windowObj.APISettings.hasUnsavedChanges(), true);

  dirty.setConfirm(false);
  await dirty.windowObj.APISettings.close();
  assert.equal(saveCalls, 0, "cancelling save-and-close discards draft without saving");
  assert.equal(dirty.dlg.open, false);
  assert.equal(dirty.windowObj.APISettings.hasUnsavedChanges(), false);

  // 再次打开并修改后确认保存并关闭
  await dirty.open();
  const mineruIn2 = dirty.nodes.get("apiKeyInput-mineru");
  mineruIn2.value = "draft-save-and-close";
  mineruIn2.trigger("input");
  dirty.setConfirm(true);
  await dirty.windowObj.APISettings.close();
  assert.equal(saveCalls, 1, "confirming saves before closing");
  assert.equal(dirty.dlg.open, false);

  console.log("credential dialog close & leave guards: OK");
})();
