"use strict";

// 统一凭据池明文查看安全组件测试：
// - 默认只展示 16 星号掩码，绝不自动请求明文；
// - 点击「查看」后通过 POST /api/settings/api-config/key/reveal (no-store + X-QB-Request) 显式拉取单条；
// - 同一时间至多展示一条，查看第二条时自动隐藏第一条；
// - 60 秒定时自动隐藏，切后台（visibilitychange）、pagehide、discard 与保存后立即清零。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");

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
  addEventListener(n, fn) {
    const arr = this.listeners.get(n) || [];
    arr.push(fn);
    this.listeners.set(n, arr);
  }
  trigger(n) {
    for (const fn of this.listeners.get(n) || []) fn({ target: this, preventDefault() {} });
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  querySelector(selector) {
    if (this.className && selector.startsWith(".") && this.className.split(/\s+/).includes(selector.slice(1))) return this;
    for (const c of this.children) {
      const hit = c.querySelector?.(selector);
      if (hit) return hit;
    }
    return null;
  }
  querySelectorAll(selector) {
    const out = [];
    const match = (n) => {
      if (selector.startsWith("[data-secret-row=")) {
        const expected = selector.slice('[data-secret-row="'.length, -2);
        return n.dataset?.secretRow === expected;
      }
      return false;
    };
    const walk = (n) => {
      if (match(n)) out.push(n);
      for (const c of n.children || []) walk(c);
    };
    walk(this);
    return out;
  }
}

(async () => {
  const nodes = new Map();
  const register = (n) => { if (n?.id) nodes.set(n.id, n); for (const c of n?.children || []) register(c); };
  const host = new El("div");
  host.id = "settingsCredentialHost";
  const orig = host.replaceChildren.bind(host);
  host.replaceChildren = (...items) => { orig(...items); for (const i of items) register(i); };
  nodes.set("settingsCredentialHost", host);

  const docEvents = {};
  const winEvents = {};
  const timers = new Map();
  let timerSeq = 0;
  const calls = [];

  const documentObj = {
    readyState: "complete",
    hidden: false,
    createElement: (t) => new El(t),
    getElementById: (id) => nodes.get(id) || null,
    addEventListener(n, fn) { (docEvents[n] ||= []).push(fn); },
    dispatchEvent() {},
  };
  const windowObj = {
    location: { pathname: "/settings", hash: "" },
    addEventListener(n, fn) { (winEvents[n] ||= []).push(fn); },
  };
  const payload = {
    catalog: {
      services: [
        { key: "mineru", label: "MinerU", roles: ["cutting"] },
        { key: "minimax", label: "MiniMax", engine: "minimax_m3", roles: ["reading", "answering"], reading_default_model: "MiniMax-M3", reading_suggested_models: [], answer_defaults: { model: "MiniMax-M3.1-Flash-Preview", base_url: "https://api.minimax.cn/v1", supports_images: true, thinking: true } },
        { key: "deepseek", label: "DeepSeek", roles: ["answering"], answer_defaults: { model: "deepseek-v4-pro", base_url: "https://api.deepseek.com", supports_images: false, thinking: true } },
      ],
      vision_order: ["minimax"],
      answer_order: ["deepseek", "minimax"],
    },
    max_accounts: 8,
    credentials: { mineru: { configured: true, count: 2 }, minimax: { configured: true, count: 1 }, deepseek: { configured: true, count: 1 } },
    reading: { primary: "minimax_m3", models: { minimax: "MiniMax-M3" }, plans: { minimax: "auto" }, readiness: { ready: true } },
    answering: { mode: "api", provider: "deepseek", features: { ai_answer: true, knowledge_tags: false }, on_intake: { answer: false } },
  };

  const sandbox = {
    window: windowObj,
    document: documentObj,
    HTMLElement: El,
    AbortController,
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    setTimeout(fn, delay) { const id = ++timerSeq; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async (url, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : null;
      calls.push({ url, method: opts.method || "GET", cache: opts.cache, headers: opts.headers, body });
      if (url === "/api/settings/api-config/key/reveal") {
        return { ok: true, status: 200, text: async () => JSON.stringify({ ...body, key: `secret-${body.service}-${body.index}` }) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(payload) };
    },
  };
  windowObj.setTimeout = sandbox.setTimeout;
  windowObj.clearTimeout = sandbox.clearTimeout;
  vm.runInNewContext(apiJs, sandbox);

  await windowObj.APISettings.mount(host, { mode: "inline" });

  const findRow = (svc, idx) => {
    const out = [];
    const walk = (n) => {
      if (n.dataset?.secretRow === `${svc}:${idx}`) out.push(n);
      for (const c of n.children || []) walk(c);
    };
    walk(host);
    return out[0];
  };
  host.querySelector = (selector) => {
    if (selector.startsWith('[data-secret-row="')) {
      const val = selector.slice('[data-secret-row="'.length, -2);
      const [s, i] = val.split(":");
      return findRow(s, Number(i));
    }
    return null;
  };

  // 默认全部掩码展示，绝不提前发 reveal 请求
  const m0 = findRow("mineru", 0);
  const m1 = findRow("mineru", 1);
  const ds0 = findRow("deepseek", 0);
  assert.equal(m0.children[1].textContent, "****************");
  assert.equal(ds0.children[1].textContent, "****************");
  assert.equal(calls.length, 1, "only initial config load fired");

  const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };

  // 1. 查看 mineru:1（严格同源 no-store POST + 60 秒自动隐藏计时器）
  m1.children[2].trigger("click");
  await flush();
  const revealCall = calls.at(-1);
  assert.equal(revealCall.url, "/api/settings/api-config/key/reveal");
  assert.equal(revealCall.method, "POST");
  assert.equal(revealCall.cache, "no-store");
  assert.equal(revealCall.headers["X-QB-Request"], "1");
  assert.deepEqual(revealCall.body, { service: "mineru", index: 1 });
  assert.equal(m1.children[1].textContent, "secret-mineru-1");
  assert.equal(m1.children[2].textContent, "隐藏");

  // 2. 查看 deepseek:0 时自动隐藏上一条 mineru:1
  ds0.children[2].trigger("click");
  await flush();
  assert.equal(m1.children[1].textContent, "****************", "previous key masked when another revealed");
  assert.equal(ds0.children[1].textContent, "secret-deepseek-0", "answer-only service uses same secure reveal");

  // 3. 触发 60000ms 定时器自动掩码
  const sixty = [...timers.entries()].find(([, v]) => v.delay === 60000);
  assert.ok(sixty, "schedules 60s auto-hide timer");
  timers.delete(sixty[0]);
  sixty[1].fn();
  assert.equal(ds0.children[1].textContent, "****************", "auto-hides after 60 seconds");

  // 4. 切后台 visibilitychange 与 pagehide 立即清零
  ds0.children[2].trigger("click");
  await flush();
  assert.equal(ds0.children[1].textContent, "secret-deepseek-0");
  documentObj.hidden = true;
  for (const fn of docEvents.visibilitychange || []) fn();
  assert.equal(ds0.children[1].textContent, "****************", "hides immediately when tab hidden");

  documentObj.hidden = false;
  ds0.children[2].trigger("click");
  await flush();
  for (const fn of winEvents.pagehide || []) fn();
  assert.equal(ds0.children[1].textContent, "****************", "hides immediately on pagehide");

  console.log("saved key 60s reveal & masking checks: OK");
})();
