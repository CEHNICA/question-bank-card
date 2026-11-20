"use strict";

// 统一密钥池自动共享验证：
// 视觉服务与答题服务同一家时（modelscope / minimax / siliconflow），底层同居于唯一 credentials.dat，
// 不再需要「共用读题密钥」按钮或跨文件复制动作；第 2 步填写/保存后第 3 步立刻反映可用。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");
const compatJs = fs.readFileSync(path.join(__dirname, "library-ai-settings.js"), "utf8");

assert.doesNotMatch(apiJs, /share-reading-key|libraryAIShareReadingKey/, "eliminates manual cross-store share button and endpoint");

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
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
  }
  append(...items) { for (const i of items) if (i && typeof i === "object") this.children.push(i); }
  replaceChildren(...items) { this.children = [...items]; }
  addEventListener(n, fn) { const a = this.listeners.get(n) || []; a.push(fn); this.listeners.set(n, a); }
  trigger(n) { for (const fn of this.listeners.get(n) || []) fn({ target: this, preventDefault() {} }); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}

function collectText(node) {
  let text = node.textContent || "";
  for (const child of node.children || []) text += " " + collectText(child);
  return text;
}

(async () => {
  const nodes = new Map();
  const reg = (n) => { if (n?.id) nodes.set(n.id, n); for (const c of n?.children || []) reg(c); };
  const host = new Node("div"); host.id = "settingsCredentialHost";
  const orig = host.replaceChildren.bind(host);
  host.replaceChildren = (...items) => { orig(...items); for (const i of items) reg(i); };
  nodes.set("settingsCredentialHost", host);

  const windowObj = { location: { pathname: "/settings", hash: "" }, setTimeout, clearTimeout, addEventListener() {} };
  const sandbox = {
    window: windowObj,
    document: { readyState: "complete", hidden: false, createElement: (t) => new Node(t), getElementById: (id) => nodes.get(id) || null, addEventListener() {}, dispatchEvent() {} },
    HTMLElement: Node,
    AbortController,
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    setTimeout,
    clearTimeout,
    fetch: async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        catalog: {
          services: [
            { key: "modelscope", label: "魔搭", engine: "modelscope_qwen", roles: ["reading", "answering"], reading_default_model: "Qwen/Qwen3.5-35B-A3B", reading_suggested_models: [], answer_defaults: { model: "Qwen/Qwen3.5-35B-A3B", base_url: "https://api-inference.modelscope.cn/v1", supports_images: true, thinking: true } },
            { key: "minimax", label: "MiniMax", engine: "minimax_m3", roles: ["reading", "answering"], reading_default_model: "MiniMax-M3", reading_suggested_models: [], answer_defaults: { model: "MiniMax-M3.1-Flash-Preview", base_url: "https://api.minimax.cn/v1", supports_images: true, thinking: true } },
          ],
          vision_order: ["modelscope", "minimax"],
          answer_order: ["minimax", "modelscope"],
        },
        max_accounts: 8,
        credentials: { modelscope: { configured: false, count: 0 }, minimax: { configured: true, count: 2 } },
        reading: { primary: "minimax_m3", models: { minimax: "MiniMax-M3" }, plans: { minimax: "auto" }, readiness: { ready: true } },
        answering: { mode: "api", provider: "minimax", features: { ai_answer: true, knowledge_tags: false }, on_intake: { answer: false } },
      }),
    }),
  };
  vm.runInNewContext(apiJs + "\n" + compatJs, sandbox);

  // 兼容门面空操作不会误抛或弄脏草稿
  windowObj.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 2 } });
  windowObj.LibraryAISettings.setReadingService({ provider: "minimax" });

  await windowObj.APISettings.mount(host);
  const answerPanel = nodes.get("credentialAnswerPanel");
  assert.ok(answerPanel);
  assert.match(collectText(answerPanel), /与读题全站共用，无需再次输入/, "answering panel automatically shares unified key without duplicate entry");
  assert.equal(windowObj.APISettings.hasUnsavedChanges(), false);

  console.log("unified shared key pool checks: OK");
})();
