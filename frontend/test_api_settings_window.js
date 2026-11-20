"use strict";

// 统一 API 配置窗口与协调层契约测试（验证单宿主渲染、无重复表单与兼容委托）
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");
const compatJs = fs.readFileSync(path.join(__dirname, "library-ai-settings.js"), "utf8");

// 确认历史冗余的三套重复表单卡片、隐藏阴影控件和 DOM 搬运容器已彻底清理
assert.doesNotMatch(html, /id="credentialUi"/, "legacy hidden #credentialUi shell is removed");
assert.doesNotMatch(html, /id="credentialVisionProvider"/, "legacy hidden vision <select> is removed");
assert.doesNotMatch(html, /id="modelSettingsForm"/, "legacy hidden <form> is removed");
assert.doesNotMatch(html, /id="libraryAIAPISettingsMount"/, "legacy second mount is removed");
assert.match(html, /id="settingsCredentialHost"/, "settings page exposes inline unified mount host");
assert.match(html, /id="credentialDialogBody"/, "modal dialog exposes body mount host");
assert.match(html, /<script src="\/static\/api-settings\.js" defer><\/script>/, "api-settings.js is loaded");

class FakeElement {
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
    this.modalOpens = 0;
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
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  querySelectorAll() {
    return [];
  }
  querySelector() {
    return null;
  }
  showModal() {
    this.open = true;
    this.modalOpens += 1;
  }
  close() {
    if (!this.open) return;
    this.open = false;
    for (const fn of this.listeners.get("close") || []) fn({});
  }
  focus() {
    this.focused = true;
  }
  scrollIntoView(opts) {
    this.scrolledWith = opts;
  }
}

function makeSandbox(pathname = "/") {
  const byId = new Map();
  const registerTree = (node) => {
    if (node?.id) byId.set(node.id, node);
    for (const child of node?.children || []) registerTree(child);
  };
  const createEl = (tag) => new FakeElement(tag);
  const credentialDialog = new FakeElement("dialog");
  credentialDialog.id = "credentialDialog";
  const credentialDialogBody = new FakeElement("div");
  credentialDialogBody.id = "credentialDialogBody";
  const origReplace = credentialDialogBody.replaceChildren.bind(credentialDialogBody);
  credentialDialogBody.replaceChildren = (...items) => {
    origReplace(...items);
    for (const item of items) registerTree(item);
  };
  byId.set("credentialDialog", credentialDialog);
  byId.set("credentialDialogBody", credentialDialogBody);

  const document = {
    readyState: "complete",
    hidden: false,
    activeElement: null,
    createElement: createEl,
    getElementById: (id) => byId.get(id) || null,
    addEventListener() {},
    dispatchEvent() {},
  };
  const windowObj = {
    location: { pathname, hash: "" },
    setTimeout,
    clearTimeout,
    addEventListener() {},
  };
  const samplePayload = {
    catalog: {
      services: [
        { key: "mineru", label: "MinerU", roles: ["cutting"], signup: "", note: "", supports_test: false },
        { key: "modelscope", label: "魔搭", engine: "modelscope_qwen", roles: ["reading", "answering"], recommended: true, reading_default_model: "Qwen/Qwen3.5-35B-A3B", reading_suggested_models: [], answer_defaults: { model: "Qwen/Qwen3.5-35B-A3B", base_url: "https://api-inference.modelscope.cn/v1", supports_images: true, thinking: true }, supports_test: true },
        { key: "deepseek", label: "DeepSeek", roles: ["answering"], answer_defaults: { model: "deepseek-v4-pro", base_url: "https://api.deepseek.com", supports_images: false, thinking: true }, supports_test: true },
      ],
      vision_order: ["modelscope"],
      answer_order: ["deepseek", "modelscope"],
    },
    max_accounts: 8,
    credentials: {
      mineru: { configured: true, count: 1 },
      modelscope: { configured: false, count: 0 },
      deepseek: { configured: false, count: 0 },
    },
    reading: { primary: "modelscope_qwen", models: { modelscope: "Qwen/Qwen3.5-35B-A3B" }, plans: { minimax: "auto" }, readiness: { ready: false } },
    answering: { mode: "assistant", provider: "deepseek", features: { ai_answer: false, knowledge_tags: false }, on_intake: { answer: false }, backlog: { missing_answer: 3 } },
  };
  const requests = [];
  const sandbox = {
    window: windowObj,
    document,
    HTMLElement: FakeElement,
    AbortController,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
    setTimeout,
    clearTimeout,
    fetch: async (url) => {
      requests.push(url);
      return { ok: true, status: 200, text: async () => JSON.stringify(samplePayload) };
    },
  };
  windowObj.setTimeout = setTimeout;
  windowObj.clearTimeout = clearTimeout;
  vm.runInNewContext(apiJs + "\n" + compatJs, sandbox);
  return { sandbox, windowObj, document, byId, requests };
}

(async () => {
  // 1. 非设置页调用 APISettings.open() 会打开统一弹窗并从 /api/settings/api-config 拉取三段配置
  const modal = makeSandbox("/");
  await modal.windowObj.APISettings.open();
  assert.equal(modal.byId.get("credentialDialog").open, true, "APISettings.open opens #credentialDialog");
  assert.equal(modal.requests[0], "/api/settings/api-config", "uses single unified config endpoint");
  assert.ok(modal.byId.get("apiSettingsReadingPanel"), "renders reading section");
  assert.ok(modal.byId.get("apiSettingsCredentialPanel"), "renders unified credential pool section");
  assert.ok(modal.byId.get("credentialAnswerPanel"), "renders answering section");

  // 2. 兼容门面 LibraryAISettings.open() 也只打开同一份统一弹窗，不再另起弹窗或第二份表单
  const compat = makeSandbox("/");
  await compat.windowObj.LibraryAISettings.open();
  assert.equal(compat.byId.get("credentialDialog").open, true, "LibraryAISettings delegates to APISettings");

  // 3. 在 /settings 页面内再次调用 open() 不会重复弹模态框
  const onSettings = makeSandbox("/settings");
  await onSettings.windowObj.APISettings.open({ section: "answering" });
  assert.equal(onSettings.byId.get("credentialDialog").open, false, "stays inline on /settings");

  console.log("unified API settings window checks: OK");
})();
