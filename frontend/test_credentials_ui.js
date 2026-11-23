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

  // 3. 默认干净状态隐藏保存按钮；输入框按 Enter 直接保存（忽略输入法选词）；
  //    连接测试返回 HTTP 409 时展示后端真实 message 而非「请求失败（HTTP 409）」。
  let enterSaveCount = 0;
  let testedReading = false;
  const uxHarness = createHarness(async (url, options) => {
    if (url === "/api/settings/api-config" && options.method === "POST") {
      enterSaveCount += 1;
      const updated = basePayload();
      updated.credentials.modelscope = { configured: true, count: 1 };
      updated.message = "API 配置已保存";
      return { ok: true, status: 200, text: async () => JSON.stringify(updated) };
    }
    if (url === "/api/settings/api-config/test" && options.method === "POST") {
      testedReading = true;
      return {
        ok: false,
        status: 409,
        text: async () => JSON.stringify({
          target: "reading",
          ok: false,
          model: "Qwen/Qwen3.5-35B-A3B",
          message: "密钥没通过：魔搭拒绝了该密钥。",
          state: basePayload(),
        }),
      };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(basePayload()) };
  });
  await uxHarness.windowObj.APISettings.mount(uxHarness.host, {
    mode: "inline",
    toast: (m, t) => uxHarness.toasts.push({ m, t }),
    confirm: async () => true,
  });
  assert.equal(uxHarness.nodes.get("credentialSave").hidden, true, "save button stays hidden when clean");
  const msEnterIn = uxHarness.nodes.get("apiKeyInput-modelscope");
  msEnterIn.value = "ms-enter-key";
  msEnterIn.trigger("input");
  assert.equal(uxHarness.nodes.get("credentialSave").hidden, false, "save button appears when dirty");
  // 输入法组词态的 Enter 不得误提交
  msEnterIn.trigger("keydown", { key: "Enter", isComposing: true });
  assert.equal(enterSaveCount, 0);
  // 正常 Enter 立即保存
  msEnterIn.trigger("keydown", { key: "Enter", isComposing: false });
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  assert.equal(enterSaveCount, 1, "pressing Enter in an input saves immediately");
  assert.equal(uxHarness.windowObj.APISettings.hasUnsavedChanges(), false);
  assert.equal(uxHarness.nodes.get("credentialSave").hidden, true, "save button hides again after save");

  // 触发读题连接测试返回 HTTP 409：不得吞掉后端 message
  const readingPanel = uxHarness.nodes.get("apiSettingsReadingPanel");
  const allButtons = [];
  const allCheckboxes = [];
  const collect = (n) => {
    if (n.tagName === "BUTTON") allButtons.push(n);
    if (n.tagName === "INPUT" && n.type === "checkbox") allCheckboxes.push(n);
    for (const c of n.children || []) collect(c);
  };
  collect(readingPanel);
  allCheckboxes[0].checked = true;
  allCheckboxes[0].trigger("change");
  const readingTestBtn = allButtons.find((b) => b.textContent.includes("看图连接"));
  readingTestBtn.trigger("click");
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  assert.equal(testedReading, true);
  assert.ok(uxHarness.toasts.some((t) => t.m.includes("密钥没通过：魔搭拒绝了该密钥")), "surfaces 409 message instead of generic HTTP 409");
  assert.ok(!uxHarness.toasts.some((t) => t.m.includes("HTTP 409")), "does not show generic HTTP 409");

  // 4. 未填密钥时禁用测试；填写后实时解锁并一步完成「保存并测试」（无需确认弹窗）；
  //    密钥输入框显示/隐藏切换正常，保存与 hideSecrets() 自动恢复掩码；「放弃更改」按钮可回退改动。
  let confirmPromptCalls = 0;
  let step4Saves = 0;
  let step4Tests = 0;
  const step4Harness = createHarness(async (url, options) => {
    if (url === "/api/settings/api-config" && options.method === "POST") {
      step4Saves += 1;
      const updated = basePayload();
      updated.credentials.modelscope = { configured: true, count: 1 };
      updated.reading.readiness = { ready: true };
      updated.message = "API 配置已保存";
      return { ok: true, status: 200, text: async () => JSON.stringify(updated) };
    }
    if (url === "/api/settings/api-config/test" && options.method === "POST") {
      step4Tests += 1;
      return {
        ok: true,
        status: 200,
        text: async () => JSON.stringify({
          target: "reading",
          ok: true,
          model: "Qwen/Qwen3.5-35B-A3B",
          message: "连接成功",
          state: { ...basePayload(), credentials: { ...basePayload().credentials, modelscope: { configured: true, count: 1 } } },
        }),
      };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(basePayload()) };
  });
  await step4Harness.windowObj.APISettings.mount(step4Harness.host, {
    mode: "inline",
    toast: (m, t) => step4Harness.toasts.push({ m, t }),
    confirm: async () => {
      confirmPromptCalls += 1;
      return true;
    },
  });

  const collectPanel = (panel) => {
    const btns = [];
    const checks = [];
    const walk = (n) => {
      if (n.tagName === "BUTTON") btns.push(n);
      if (n.tagName === "INPUT" && n.type === "checkbox") checks.push(n);
      for (const c of n.children || []) walk(c);
    };
    walk(panel);
    return { btns, checks };
  };

  let readingUi = collectPanel(step4Harness.nodes.get("apiSettingsReadingPanel"));
  let readTestBtn = readingUi.btns.find((b) => b.textContent.includes("看图连接"));
  let readConsentBox = readingUi.checks[0];
  assert.equal(readTestBtn.disabled, true, "reading test button disabled when key is missing");
  assert.equal(readConsentBox.disabled, true, "reading consent checkbox disabled when key is missing");
  assert.equal(step4Harness.nodes.get("credentialDiscard").hidden, true, "discard button hidden when clean");

  // 切换本地密钥输入框显示/隐藏
  const msInput4 = step4Harness.nodes.get("apiKeyInput-modelscope");
  const visBtn4 = msInput4.parentElement.children[1];
  assert.equal(msInput4.type, "password");
  visBtn4.trigger("click");
  assert.equal(msInput4.type, "text", "toggles input visibility locally");
  assert.equal(visBtn4.textContent, "隐藏");

  // 输入密钥后，实时解锁读题测试、出现「放弃更改」且按钮变为「保存并测试」
  msInput4.value = "ms-one-click-key";
  msInput4.trigger("input");
  assert.equal(step4Harness.nodes.get("credentialDiscard").hidden, false, "discard button appears when dirty");
  assert.equal(readConsentBox.disabled, false, "consent checkbox unlocks as soon as key is entered");
  assert.match(readTestBtn.textContent, /保存并测试.*看图连接/);

  // 放弃更改恢复干净状态并掩码输入
  step4Harness.nodes.get("credentialDiscard").trigger("click");
  for (let i = 0; i < 6; i += 1) await Promise.resolve();
  assert.equal(step4Harness.windowObj.APISettings.hasUnsavedChanges(), false);
  assert.equal(step4Harness.nodes.get("credentialDiscard").hidden, true);
  assert.equal(step4Harness.nodes.get("apiKeyInput-modelscope").type, "password", "discard restores password masking");

  // 再次输入并直接勾选同意 -> 一步保存并测试，不弹确认框
  const msInput5 = step4Harness.nodes.get("apiKeyInput-modelscope");
  msInput5.value = "ms-one-click-key";
  msInput5.trigger("input");
  readingUi = collectPanel(step4Harness.nodes.get("apiSettingsReadingPanel"));
  readTestBtn = readingUi.btns.find((b) => b.textContent.includes("看图连接"));
  readConsentBox = readingUi.checks[0];
  readConsentBox.checked = true;
  readConsentBox.trigger("change");
  assert.equal(readTestBtn.disabled, false);
  readTestBtn.trigger("click");
  for (let i = 0; i < 16; i += 1) await Promise.resolve();
  assert.equal(confirmPromptCalls, 0, "does not show redundant save-before-test confirm dialog");
  assert.equal(step4Saves, 1, "saves automatically before testing");
  assert.equal(step4Tests, 1, "runs connection test right after save");

  console.log("unified credentials UI tests: OK");
})();
