"use strict";

// 统一凭据与模型表单提交契约测试（单次保存、错误处理与状态不丢失）
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");

class MockElement {
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
    this.type = "";
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
  }
  append(...items) {
    for (const item of items) {
      if (item && typeof item === "object") {
        this.children.push(item);
        item.parentElement = this;
      }
    }
  }
  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }
  addEventListener(name, fn) {
    const list = this.listeners.get(name) || [];
    list.push(fn);
    this.listeners.set(name, list);
  }
  trigger(name, detail = {}) {
    for (const fn of this.listeners.get(name) || []) {
      fn({ target: this, preventDefault() {}, ...detail });
    }
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
  }
  querySelectorAll() {
    return [];
  }
  querySelector() {
    return null;
  }
  showModal() {
    this.open = true;
  }
  close() {
    this.open = false;
  }
  focus() {}
}

function createHarness(responder) {
  const nodes = new Map();
  const register = (node) => {
    if (node?.id) nodes.set(node.id, node);
    for (const c of node?.children || []) register(c);
  };
  const host = new MockElement("div");
  host.id = "settingsCredentialHost";
  const origReplace = host.replaceChildren.bind(host);
  host.replaceChildren = (...items) => {
    origReplace(...items);
    for (const item of items) register(item);
  };
  nodes.set("settingsCredentialHost", host);

  const events = [];
  const toasts = [];
  const document = {
    readyState: "complete",
    hidden: false,
    createElement: (tag) => new MockElement(tag),
    getElementById: (id) => nodes.get(id) || null,
    addEventListener() {},
    dispatchEvent(evt) {
      events.push(evt);
    },
  };
  const windowObj = {
    location: { pathname: "/settings", hash: "" },
    setTimeout,
    clearTimeout,
    addEventListener() {},
  };
  const sandbox = {
    window: windowObj,
    document,
    HTMLElement: MockElement,
    AbortController,
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    setTimeout,
    clearTimeout,
    fetch: async (url, options = {}) => responder(url, options),
  };
  vm.runInNewContext(apiJs, sandbox);
  return { windowObj, nodes, host, toasts, events };
}

function basePayload() {
  return {
    catalog: {
      services: [
        { key: "mineru", label: "MinerU", roles: ["cutting"], signup: "", note: "" },
        { key: "modelscope", label: "魔搭", engine: "modelscope_qwen", roles: ["reading", "answering"], recommended: true, reading_default_model: "Qwen/Qwen3.5-35B-A3B", reading_suggested_models: ["Qwen/Qwen3.5-35B-A3B"], answer_defaults: { model: "Qwen/Qwen3.5-35B-A3B", base_url: "https://api-inference.modelscope.cn/v1", supports_images: true, thinking: true } },
        { key: "minimax", label: "MiniMax", engine: "minimax_m3", roles: ["reading", "answering"], reading_default_model: "MiniMax-M3", reading_suggested_models: ["MiniMax-M3"], answer_defaults: { model: "MiniMax-M3.1-Flash-Preview", base_url: "https://api.minimax.cn/v1", supports_images: true, thinking: true } },
        { key: "siliconflow", label: "硅基流动", engine: "siliconflow_qwen3", roles: ["reading", "answering"], reading_default_model: "Qwen/Qwen3-VL-32B-Instruct", reading_suggested_models: [], answer_defaults: { model: "Qwen/Qwen3-VL-32B-Instruct", base_url: "https://api.siliconflow.cn/v1", supports_images: true, thinking: false } },
        { key: "deepseek", label: "DeepSeek", roles: ["answering"], answer_defaults: { model: "deepseek-v4-pro", base_url: "https://api.deepseek.com", supports_images: false, thinking: true } },
        { key: "doubao", label: "豆包", roles: ["answering"], answer_defaults: { model: "ep-1234", base_url: "https://ark.cn-beijing.volces.com/api/v3", supports_images: false, thinking: true } },
        { key: "custom", label: "其他兼容服务", roles: ["answering"], answer_defaults: { model: "", base_url: "", supports_images: false, thinking: true } },
      ],
      vision_order: ["modelscope", "minimax", "siliconflow"],
      answer_order: ["deepseek", "minimax", "doubao", "custom", "modelscope", "siliconflow"],
    },
    max_accounts: 8,
    credentials: {
      mineru: { configured: true, count: 1 },
      modelscope: { configured: false, count: 0 },
      minimax: { configured: false, count: 0 },
      siliconflow: { configured: false, count: 0 },
      deepseek: { configured: false, count: 0 },
      doubao: { configured: false, count: 0 },
      custom: { configured: false, count: 0 },
    },
    reading: { primary: "modelscope_qwen", models: { modelscope: "Qwen/Qwen3.5-35B-A3B", minimax: "MiniMax-M3", siliconflow: "Qwen/Qwen3-VL-32B-Instruct" }, plans: { minimax: "auto" }, readiness: { ready: false } },
    answering: { mode: "assistant", provider: "deepseek", features: { ai_answer: false, knowledge_tags: false }, on_intake: { answer: false } },
  };
}

(async () => {
  // 1. 一次保存同时下发 credentials + reading + answering，且支持多账号分号切分
  const posts = [];
  const okHarness = createHarness(async (url, options) => {
    if (options.method === "POST") {
      const body = JSON.parse(options.body);
      posts.push({ url, body });
      const updated = basePayload();
      updated.credentials.modelscope = { configured: true, count: 2 };
      updated.credentials.deepseek = { configured: true, count: 1 };
      updated.answering.mode = "api";
      updated.message = "API 配置已保存";
      return { ok: true, status: 200, text: async () => JSON.stringify(updated) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(basePayload()) };
  });

  await okHarness.windowObj.APISettings.mount(okHarness.host, {
    mode: "inline",
    toast: (m, t) => okHarness.toasts.push({ m, t }),
    confirm: async () => true,
  });

  // 全站 7 家服务均渲染在统一清单内
  for (const svc of ["mineru", "modelscope", "minimax", "siliconflow", "deepseek", "doubao", "custom"]) {
    assert.ok(okHarness.nodes.get(`apiKeyInput-${svc}`), `input for ${svc} exists`);
  }

  const msInput = okHarness.nodes.get("apiKeyInput-modelscope");
  msInput.value = "ms-tok-1 ; ms-tok-2";
  msInput.trigger("input");

  const dsInput = okHarness.nodes.get("apiKeyInput-deepseek");
  dsInput.value = "ds-key-1";
  dsInput.trigger("input");

  assert.equal(okHarness.windowObj.APISettings.hasUnsavedChanges(), true, "tracks dirty state after typing keys");

  const saved = await okHarness.windowObj.APISettings.save();
  assert.equal(saved, true);
  assert.equal(posts.length, 1, "submits one unified POST /api/settings/api-config");
  assert.equal(posts[0].url, "/api/settings/api-config");
  assert.deepEqual(posts[0].body.credentials.modelscope, { action: "replace", accounts: ["ms-tok-1", "ms-tok-2"] });
  assert.deepEqual(posts[0].body.credentials.deepseek, { action: "replace", accounts: ["ds-key-1"] });
  assert.deepEqual(posts[0].body.credentials.mineru, { action: "keep" });
  assert.equal(okHarness.windowObj.APISettings.hasUnsavedChanges(), false, "dirty state cleared after save");
  assert.ok(okHarness.events.some((e) => e.type === "api-config-saved"), "broadcasts unified saved event");

  // 2. 保存失败时必须保留用户输入，不能静默清空未保存改动
  const failHarness = createHarness(async (url, options) => {
    if (options.method === "POST") {
      return { ok: false, status: 400, text: async () => JSON.stringify({ error: "API 凭据格式错误" }) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(basePayload()) };
  });
  await failHarness.windowObj.APISettings.mount(failHarness.host, {
    toast: (m, t) => failHarness.toasts.push({ m, t }),
  });
  const mineruIn = failHarness.nodes.get("apiKeyInput-mineru");
  mineruIn.value = "unsaved bad token";
  mineruIn.trigger("input");
  const failedSave = await failHarness.windowObj.APISettings.save();
  assert.equal(failedSave, false);
  assert.equal(failHarness.windowObj.APISettings.hasUnsavedChanges(), true, "preserves unsaved changes on error");
  assert.match(failHarness.nodes.get("credentialResult").textContent, /API 凭据格式错误/);

  console.log("unified credentials UI tests: OK");
})();
