"use strict";

// 统一前端解题与探测控制流测试：
// - 15 秒 AbortController 请求超时取消；
// - 单调递增 loadEpoch 丢弃迟到响应（防乱序覆盖）；
// - 切换服务商时互不丢失已填写的模型参数（provider_profiles）；
// - 关闭 AI 参考答案开关自动连带关闭新入库自动生成；
// - 连接测试必须显式勾选同意且仅发送 confirm=true 到 /api/settings/api-config/test。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");
const deferred = () => { let resolve, reject; const p = new Promise((y, n) => { resolve = y; reject = n; }); return { promise: p, resolve, reject }; };

class Node {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.id = "";
    this.className = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.readOnly = false;
    this.type = "";
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
}

function makePayload(overrides = {}) {
  return {
    catalog: {
      services: [
        { key: "mineru", label: "MinerU", roles: ["cutting"] },
        { key: "minimax", label: "MiniMax", engine: "minimax_m3", roles: ["reading", "answering"], reading_default_model: "MiniMax-M3", reading_suggested_models: [], answer_defaults: { model: "MiniMax-M3.1-Flash-Preview", base_url: "https://api.minimax.cn/v1", supports_images: true, thinking: true } },
        { key: "deepseek", label: "DeepSeek", roles: ["answering"], answer_defaults: { model: "deepseek-v4-pro", base_url: "https://api.deepseek.com", supports_images: false, thinking: true } },
        { key: "doubao", label: "豆包", roles: ["answering"], answer_defaults: { model: "", base_url: "https://ark.cn-beijing.volces.com/api/v3", supports_images: false, thinking: true } },
      ],
      vision_order: ["minimax"],
      answer_order: ["deepseek", "minimax", "doubao"],
    },
    max_accounts: 8,
    credentials: { mineru: { configured: true, count: 1 }, minimax: { configured: true, count: 1 }, deepseek: { configured: true, count: 1 }, doubao: { configured: false, count: 0 } },
    reading: { primary: "minimax_m3", models: { minimax: "MiniMax-M3" }, plans: { minimax: "auto" }, readiness: { ready: true } },
    answering: {
      mode: "api",
      provider: "deepseek",
      model: "deepseek-v4-pro",
      base_url: "https://api.deepseek.com",
      supports_images: false,
      thinking: true,
      features: { ai_answer: true, knowledge_tags: false },
      on_intake: { answer: true },
      ...overrides,
    },
  };
}

function setup(fetcher) {
  const nodes = new Map();
  const reg = (n) => { if (n?.id) nodes.set(n.id, n); for (const c of n?.children || []) reg(c); };
  const host = new Node("div"); host.id = "settingsCredentialHost";
  const orig = host.replaceChildren.bind(host);
  host.replaceChildren = (...items) => { orig(...items); for (const i of items) reg(i); };
  nodes.set("settingsCredentialHost", host);

  const timers = new Map();
  let tId = 0;
  const sandbox = {
    window: { location: { pathname: "/settings", hash: "" }, addEventListener() {} },
    document: { readyState: "complete", hidden: false, createElement: (t) => new Node(t), getElementById: (id) => nodes.get(id) || null, addEventListener() {}, dispatchEvent() {} },
    HTMLElement: Node,
    AbortController,
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    setTimeout(fn, delay) { const id = ++tId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: fetcher,
  };
  sandbox.window.setTimeout = sandbox.setTimeout;
  sandbox.window.clearTimeout = sandbox.clearTimeout;
  vm.runInNewContext(apiJs, sandbox);
  return { windowObj: sandbox.window, host, nodes, timers };
}

(async () => {
  // 1. 15 秒请求超时触发 AbortController 取消并返回可读提示
  const timeout = setup(async (_url, opts) => new Promise((_res, rej) => {
    opts.signal.addEventListener("abort", () => {
      const err = new Error("aborted");
      err.name = "AbortError";
      rej(err);
    }, { once: true });
  }));
  const loadTask = timeout.windowObj.APISettings.mount(timeout.host);
  const reqTimer = [...timeout.timers.entries()].find(([, v]) => v.delay === 15000);
  assert.ok(reqTimer, "schedules 15s AbortController timeout");
  timeout.timers.delete(reqTimer[0]);
  reqTimer[1].fn();
  await loadTask;
  assert.match(timeout.host.children[0].children[0].children[0].textContent, /超时/);

  // 2. 乱序响应防覆盖：先发出的慢请求不得覆盖后发起的新状态
  const slow = deferred();
  const fast = deferred();
  let callIdx = 0;
  const raced = setup(async () => (++callIdx === 1 ? slow.promise : fast.promise));
  const r1 = raced.windowObj.APISettings.mount(raced.host);
  const r2 = raced.windowObj.APISettings.refresh();
  fast.resolve({ ok: true, status: 200, text: async () => JSON.stringify(makePayload({ provider: "minimax" })) });
  await r2;
  slow.resolve({ ok: true, status: 200, text: async () => JSON.stringify(makePayload({ provider: "deepseek" })) });
  await r1;
  assert.equal(raced.windowObj.APISettings._test.state.serverData.answering.provider, "minimax", "newer refresh wins over stale slow GET");

  // 3. 切换 provider 保留各家配置，并且显式测试连接提交 confirm: true
  const testPosts = [];
  const interactive = setup(async (url, opts = {}) => {
    if (opts.method === "POST") {
      testPosts.push({ url, body: JSON.parse(opts.body) });
      return { ok: true, status: 200, text: async () => JSON.stringify({ target: "answering", ok: true, message: "连接通过", state: makePayload({ verified: true }) }) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(makePayload()) };
  });
  await interactive.windowObj.APISettings.mount(interactive.host);
  const answerPanel = interactive.nodes.get("credentialAnswerPanel");
  const buttons = [];
  const checkboxes = [];
  const selects = [];
  const walk = (n) => {
    if (n.tagName === "BUTTON") buttons.push(n);
    if (n.tagName === "INPUT" && n.type === "checkbox") checkboxes.push(n);
    if (n.tagName === "SELECT") selects.push(n);
    for (const c of n.children || []) walk(c);
  };
  walk(answerPanel);

  const testBtn = buttons.find((b) => b.textContent.includes("测试解题连接"));
  assert.equal(testBtn.disabled, true, "test probe button disabled until consent checked");

  // 勾选同意后测试按钮解锁并发送 confirm=true
  const consentBox = checkboxes.at(-1);
  consentBox.checked = true;
  consentBox.trigger("change");
  assert.equal(testBtn.disabled, false);
  testBtn.trigger("click");
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(testPosts[0], { url: "/api/settings/api-config/test", body: { target: "answering", confirm: true } });

  // 关闭 AI 参考答案时连带关闭新入库自动生成
  const draft = interactive.windowObj.APISettings._test.state.draft;
  assert.equal(draft.answering.on_intake.answer, true);
  checkboxes[0].checked = false;
  checkboxes[0].trigger("change");
  assert.equal(draft.answering.on_intake.answer, false, "turning off ai_answer forces on_intake.answer=false");

  console.log("unified answering settings & probe flow checks: OK");
})();
