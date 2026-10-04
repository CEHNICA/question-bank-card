"use strict";

// 同一家 MiniMax 的密钥只填一次。
//
// 过去读题和答案各存一份加密文件，同一个 Key 要粘贴两次。这里是那条显式的
// 路：老师点一下才复制，读题那边没存就明说去哪填，已经有一份独立密钥时按钮
// 写的是“改用”而不是“共用”，共用之后也随时能改回来。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function flush() { for (let i = 0; i < 12; i += 1) await Promise.resolve(); }

function setup({ reading = {} } = {}) {
  const elements = new Map();
  class Element {
    constructor(tag = "div") {
      this.value = ""; this.checked = false; this.disabled = false; this.open = false;
      this.tagName = tag.toUpperCase(); this.children = []; this.modalOpens = 0;
      this.attributes = {}; this.listeners = new Map(); this.classList = { toggle() {}, add() {}, remove() {} };
    }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    set innerHTML(html) {
      this.html = html;
      for (const match of html.matchAll(/<[^>]+id="([^"]+)"[^>]*>/g)) {
        const child = new Element();
        child.disabled = /\sdisabled(?:\s|>)/.test(match[0]);
        child.hidden = /\shidden(?:\s|>)/.test(match[0]);
        child.readOnly = /\sreadonly(?:\s|>)/.test(match[0]);
        child.type = /\stype="([^"]+)"/.exec(match[0])?.[1];
        elements.set(match[1], child);
      }
    }
    addEventListener(name, fn) { this.listeners.set(name, [...(this.listeners.get(name) || []), fn]); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    append(...children) { for (const child of children) { this.children.push(child); child.parentElement = this; } }
    replaceChildren(...children) { this.children = [...children]; }
    showModal() { this.open = true; this.modalOpens++; }
    close() { this.open = false; this.trigger("close"); }
    trigger(name, detail = {}) { for (const fn of this.listeners.get(name) || []) fn({ preventDefault() {}, ...detail }); }
  }
  const document = { head: new Element(), body: new Element(), listeners: new Map(), events: [],
    createElement: (tag) => new Element(tag), getElementById: (id) => elements.get(id),
    addEventListener(name, fn) { this.listeners.set(name, fn); }, dispatchEvent(event) { this.events.push(event); } };
  const current = { mode: "api", provider: "minimax", base_url: "https://api.minimax.cn/v1",
    model: "MiniMax-M3.1-Flash-Preview", configured: true, key_configured: false, ready: false,
    api_ready: false, verified: false, supports_images: true, thinking: true,
    features: { knowledge_tags: false, ai_answer: false }, on_intake: { tags: false, answer: false },
    message: "offline", shareable_from_reading: ["minimax"],
    keys: { deepseek: { configured: false, count: 0, shared_with_reading: false },
      minimax: { configured: false, count: 0, shared_with_reading: false },
      doubao: { configured: false, count: 0, shared_with_reading: false },
      custom: { configured: false, count: 0, shared_with_reading: false } } };
  const calls = [];
  let body = { ...current, keys: JSON.parse(JSON.stringify(current.keys)) };
  const window = { location: { hash: "" }, listeners: new Map(),
    addEventListener(name, fn) { this.listeners.set(name, fn); } };
  const parent = new Element("dialog"), host = new Element("section");
  parent.id = "apiSettingsDialog"; parent.append(host); document.body.append(parent);
  window.APISettings = { async open() { if (!parent.open) parent.showModal(); await window.LibraryAISettings.mount(host, { embedded: true }); } };
  const sandbox = { window, document, AbortController, setTimeout: (fn) => fn, clearTimeout() {},
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : undefined;
      calls.push({ url, payload });
      return { ok: true, json: async () => body };
    } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "library-ai-settings.js"), "utf8"), sandbox);
  return { window, document, calls, get: (id) => elements.get(id), reading,
    setBody: (next) => { body = next; }, getBody: () => body };
}

const shared = (extra = {}) => ({ ...JSON.parse(JSON.stringify({})), ...extra });

(async () => {
  // 1. 读题那边没存 MiniMax 密钥：整块出现，但按钮是灰的，并说清去哪填。
  const none = setup();
  none.window.LibraryAISettings.setReadingKeys({ minimax: { configured: false, count: 0 } });
  await none.window.LibraryAISettings.open(); await flush();
  assert.equal(none.get("libraryAIShareKey").hidden, false, "MiniMax is the one shareable service, so the block appears");
  assert.equal(none.get("libraryAIShareReadingKey").hidden, true, "a dead button is worse than no button");
  assert.match(none.get("libraryAIShareHelp").textContent, /还没有保存/);

  // 2. 读题那边有密钥：给出“只填一次”的那条路。
  const ready = setup();
  ready.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 2 } });
  await ready.window.LibraryAISettings.open(); await flush();
  assert.equal(ready.get("libraryAIShareReadingKey").hidden, false);
  assert.equal(ready.get("libraryAIShareReadingKey").textContent, "共用读题的 MiniMax 密钥");
  assert.match(ready.get("libraryAIShareHelp").textContent, /不用再粘贴一次/);

  ready.get("libraryAIShareReadingKey").trigger("click"); await flush();
  const share = ready.calls.find((call) => call.url.endsWith("/share-reading-key"));
  assert.ok(share, "the button performs the explicit copy");
  assert.deepEqual(share.payload, { provider: "minimax" });
  assert.equal(ready.get("libraryAIResult").textContent.includes("已共用"), true);

  // 3. 共用之后说实话：这里用的就是读题那份，而且不会自动跟着变。
  const sharedState = { ...ready.getBody(), key_configured: true, verified: false,
    keys: { ...ready.getBody().keys, minimax: { configured: true, count: 1, shared_with_reading: true } } };
  const after = setup();
  after.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 2 } });
  after.setBody(sharedState);
  await after.window.LibraryAISettings.open(); await flush();
  assert.equal(after.get("libraryAIShareReadingKey").hidden, true, "already shared: no second copy button");
  assert.equal(after.get("libraryAIKeepOwnKey").hidden, false, "and a way back");
  assert.match(after.get("libraryAIShareHelp").textContent, /不会自动跟着变/);

  after.get("libraryAIKeepOwnKey").trigger("click"); await flush();
  const undo = after.calls.find((call) => call.url.endsWith("/library-ai") && call.payload);
  assert.deepEqual(undo.payload, { key: { action: "clear" } },
    "going back clears only the answers copy; the reading key is untouched");

  // 4. 已经有一份独立密钥时，按钮写的是“改用”，不是“共用”。
  const own = { ...sharedState,
    keys: { ...sharedState.keys, minimax: { configured: true, count: 1, shared_with_reading: false } } };
  const independent = setup();
  independent.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 1 } });
  independent.setBody(own);
  await independent.window.LibraryAISettings.open(); await flush();
  assert.equal(independent.get("libraryAIShareReadingKey").textContent, "改用读题的 MiniMax 密钥");
  assert.match(independent.get("libraryAIShareHelp").textContent, /已经有一份独立的/);
  assert.equal(independent.get("libraryAIKeepOwnKey").hidden, true);

  // 5. 不是 MiniMax 的服务商没有这条路：DeepSeek 只管答案，豆包也是。
  const deepseek = { ...own, provider: "deepseek", shareable_from_reading: [] };
  const other = setup();
  other.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 1 } });
  other.setBody(deepseek);
  await other.window.LibraryAISettings.open(); await flush();
  assert.equal(other.get("libraryAIShareKey").hidden, true,
    "merging a service that only answers questions would invent a capability the app does not have");

  console.log("shared api key ok");
})();
