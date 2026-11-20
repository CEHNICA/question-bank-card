"use strict";

// 统一保存性能与防卡顿测试：
// 关闭或保存只需等待单次 POST /api/settings/api-config，绝不被异步状态刷新串行阻塞；
// 等待期间界面立即展示保存状态并锁定重复提交与关闭按钮。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

class Node {
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
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
  }
  append(...items) { for (const i of items) if (i && typeof i === "object") { this.children.push(i); i.parentElement = this; } }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  addEventListener(n, fn) { const a = this.listeners.get(n) || []; a.push(fn); this.listeners.set(n, a); }
  trigger(n) { for (const fn of this.listeners.get(n) || []) fn({ target: this, preventDefault() {} }); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
}

const sample = {
  catalog: { services: [{ key: "mineru", label: "MinerU", roles: ["cutting"] }], vision_order: [], answer_order: [] },
  max_accounts: 8,
  credentials: { mineru: { configured: false, count: 0 } },
  reading: { primary: "assistant", models: {}, plans: { minimax: "auto" }, readiness: { ready: true } },
  answering: { mode: "assistant", provider: "deepseek", features: { ai_answer: false, knowledge_tags: false }, on_intake: { answer: false } },
};

(async () => {
  const nodes = new Map();
  const reg = (n) => { if (n?.id) nodes.set(n.id, n); for (const c of n?.children || []) reg(c); };
  const dlg = new Node("dialog"); dlg.id = "credentialDialog";
  const body = new Node("div"); body.id = "credentialDialogBody";
  const orig = body.replaceChildren.bind(body);
  body.replaceChildren = (...items) => { orig(...items); for (const i of items) reg(i); };
  nodes.set("credentialDialog", dlg); nodes.set("credentialDialogBody", body);

  const postGate = deferred();
  const slowStatusGate = deferred();
  let statusRefreshCount = 0;
  const requests = [];
  const sandbox = {
    window: { location: { pathname: "/", hash: "" }, setTimeout, clearTimeout, addEventListener() {} },
    document: { readyState: "complete", hidden: false, createElement: (t) => new Node(t), getElementById: (id) => nodes.get(id) || null, addEventListener() {}, dispatchEvent() {} },
    HTMLElement: Node,
    AbortController,
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    setTimeout,
    clearTimeout,
    fetch: async (url, opts = {}) => {
      requests.push({ url, method: opts.method || "GET" });
      if (opts.method === "POST") return postGate.promise;
      return { ok: true, status: 200, text: async () => JSON.stringify(sample) };
    },
  };
  vm.runInNewContext(apiJs, sandbox);

  await sandbox.window.APISettings.open({
    confirm: async () => true,
    onStatusChange: () => { statusRefreshCount += 1; return slowStatusGate.promise; },
  });
  const mineruIn = nodes.get("apiKeyInput-mineru");
  mineruIn.value = "tok-fast";
  mineruIn.trigger("input");

  requests.length = 0;
  const closeTask = sandbox.window.APISettings.close();
  for (let i = 0; i < 6; i += 1) await Promise.resolve();

  // 等待保存期间：窗体仍打开、按钮禁用且提示可见
  assert.equal(dlg.open, true, "dialog waits only for the single save round-trip");
  assert.equal(nodes.get("credentialSave").disabled, true, "save button disabled while saving");
  assert.equal(nodes.get("credentialCancel").disabled, true, "cancel button disabled while saving");
  assert.match(nodes.get("credentialResult").textContent, /正在加密保存/);

  postGate.resolve({ ok: true, status: 200, text: async () => JSON.stringify({ ...sample, message: "saved" }) });
  const closed = await closeTask;

  // 保存完成后立刻关闭，不等待缓慢的后台状态刷新（slowStatusGate 仍未 resolve）
  assert.equal(closed, true);
  assert.equal(dlg.open, false, "dialog closes without blocking on async status refresh");
  assert.equal(statusRefreshCount, 1, "status refresh was still triggered asynchronously");
  assert.deepEqual(requests, [{ url: "/api/settings/api-config", method: "POST" }]);

  slowStatusGate.resolve(true);
  console.log("credential close lag checks: OK");
})();
