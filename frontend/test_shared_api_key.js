"use strict";

// 魔搭、MiniMax 和硅基流动均可主动复用读题密钥；打开窗口不替用户换服务。
// 此测试只用隔离 DOM 和合成状态，不读取真实密钥或访问云端。

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
    focus() { this.focused = true; }
    trigger(name, detail = {}) { for (const fn of this.listeners.get(name) || []) fn({ preventDefault() {}, ...detail }); }
  }
  const document = { head: new Element(), body: new Element(), listeners: new Map(), events: [],
    createElement: (tag) => new Element(tag), getElementById: (id) => elements.get(id),
    addEventListener(name, fn) { this.listeners.set(name, fn); }, dispatchEvent(event) { this.events.push(event); } };
  const current = { mode: "api", provider: "minimax", base_url: "https://api.minimax.cn/v1",
    model: "MiniMax-M3.1-Flash-Preview", configured: true, key_configured: false, ready: false,
    api_ready: false, verified: false, supports_images: true, thinking: true,
    features: { knowledge_tags: false, ai_answer: false }, knowledge: { total: 73, chapters: 18, file: "C:/data/knowledge-points.txt" },
    on_intake: { tags: false, answer: false }, backlog: { tags: 12, answer: 5, total: 40 },
    message: "offline", shareable_from_reading: ["modelscope", "minimax", "siliconflow"],
    keys: { deepseek: { configured: false, count: 0, shared_with_reading: false },
      minimax: { configured: false, count: 0, shared_with_reading: false },
      doubao: { configured: false, count: 0, shared_with_reading: false },
      custom: { configured: false, count: 0, shared_with_reading: false } } };
  const calls = [];
  let body = { ...current, keys: JSON.parse(JSON.stringify(current.keys)) };
  let confirm = true, respond;
  const window = { location: { hash: "" }, listeners: new Map(), confirm: () => confirm,
    addEventListener(name, fn) { this.listeners.set(name, fn); } };
  const parent = new Element("dialog"), host = new Element("section");
  parent.id = "apiSettingsDialog"; parent.append(host); document.body.append(parent);
  window.APISettings = { async open() { if (!parent.open) parent.showModal(); await window.LibraryAISettings.mount(host, { embedded: true }); } };
  const sandbox = { window, document, AbortController, setTimeout: (fn) => fn, clearTimeout() {},
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : undefined;
      calls.push({ url, payload });
      const result = respond ? await respond(url, payload) : body;
      return { ok: true, json: async () => result };
    } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "library-ai-settings.js"), "utf8"), sandbox);
  return { window, document, calls, get: (id) => elements.get(id), reading,
    setBody: (next) => { body = next; }, getBody: () => body,
    response: fn => { respond = fn; }, consent: value => { confirm = value; } };
}

(async () => {
  // 1. 读题那边没存 MiniMax 密钥：整块出现，但按钮是灰的，并说清去哪填。
  const none = setup();
  none.window.LibraryAISettings.setReadingKeys({ minimax: { configured: false, count: 0 } });
  await none.window.LibraryAISettings.open(); await flush();
  assert.equal(none.get("libraryAIShareKey").hidden, false, "The selected supported service explains how reuse works");
  assert.equal(none.get("libraryAIShareReadingKey").hidden, true, "a dead button is worse than no button");
  assert.match(none.get("libraryAIShareHelp").textContent, /先到.*保存.*密钥/);

  // 2. 读题那边有密钥：给出“只填一次”的那条路。
  const ready = setup();
  ready.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 2 } });
  await ready.window.LibraryAISettings.open(); await flush();
  assert.equal(ready.get("libraryAIShareReadingKey").hidden, false);
  assert.equal(ready.get("libraryAIShareReadingKey").textContent, "复用读题的MiniMax密钥");
  assert.match(ready.get("libraryAIShareHelp").textContent, /不需要再次粘贴/);

  ready.get("libraryAIShareReadingKey").trigger("click"); await flush();
  const share = ready.calls.find((call) => call.url.endsWith("/share-reading-key"));
  assert.ok(share, "the button performs the explicit copy");
  assert.deepEqual(share.payload, { provider: "minimax" });
  assert.equal(ready.get("libraryAIResult").textContent.includes("已复用"), true);

  // 3. 共用之后说实话：这里用的就是读题那份，而且不会自动跟着变。
  const sharedState = { ...ready.getBody(), key_configured: true, verified: false,
    keys: { ...ready.getBody().keys, minimax: { configured: true, count: 1, shared_with_reading: true } } };
  const after = setup();
  after.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 2 } });
  after.setBody(sharedState);
  await after.window.LibraryAISettings.open(); await flush();
  assert.equal(after.get("libraryAIShareReadingKey").hidden, false, "Already reused credentials can be explicitly refreshed after a reading-side change");
  assert.equal(after.get("libraryAIKeepOwnKey").hidden, false, "and a way back");
  assert.match(after.get("libraryAIShareHelp").textContent, /不会自动跟着更改/);

  const beforeOwnKey = after.calls.length;
  after.get("libraryAIKeepOwnKey").trigger("click"); await flush();
  assert.equal(after.calls.length, beforeOwnKey, "Choosing to enter an independent key preserves saved credentials until explicit save");
  assert.equal(after.get("libraryAIKey-minimax").focused, true);
  assert.equal(after.window.LibraryAISettings.hasUnsavedChanges(), false, "Focusing the replacement input does not manufacture a configuration change");

  // 4. 已经有一份独立密钥时，按钮写的是“改用”，不是“共用”。
  const own = { ...sharedState,
    keys: { ...sharedState.keys, minimax: { configured: true, count: 1, shared_with_reading: false } } };
  const independent = setup();
  independent.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 1 } });
  independent.setBody(own);
  await independent.window.LibraryAISettings.open(); await flush();
  assert.equal(independent.get("libraryAIShareReadingKey").textContent, "重新复用读题的MiniMax密钥");
  assert.match(independent.get("libraryAIShareHelp").textContent, /当前有独立的/);
  assert.equal(independent.get("libraryAIKeepOwnKey").hidden, true);

  // 5. DeepSeek、豆包和自定义答案 API 不会凭空获得读题密钥来源。
  const deepseek = { ...own, provider: "deepseek", shareable_from_reading: [] };
  const other = setup();
  other.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 1 } });
  other.setBody(deepseek);
  await other.window.LibraryAISettings.open(); await flush();
  assert.equal(other.get("libraryAIShareKey").hidden, true,
    "merging a service that only answers questions would invent a capability the app does not have");

  // 6. 旧 DeepSeek 配置保持原样；三家看图服务都只有明确点击才切换与复用。
  const names = { modelscope: "魔搭", minimax: "MiniMax", siliconflow: "硅基流动" };
  for (const provider of Object.keys(names)) {
    const reusable = setup(), previous = { ...reusable.getBody(), provider: "deepseek", base_url: "https://api.deepseek.com", model: "saved-old-math-model", supports_images: false };
    reusable.setBody(previous);
    reusable.window.LibraryAISettings.setReadingKeys({ [provider]: { configured: true, count: 2 } });
    reusable.window.LibraryAISettings.setReadingService({ provider, models: { [provider]: "saved-reading-model" } });
    await reusable.window.LibraryAISettings.open(); await flush();
    assert.equal(reusable.get("libraryAIProvider").value, "deepseek");
    assert.equal(reusable.get("libraryAIModel").value, previous.model);
    assert.equal(reusable.window.LibraryAISettings.hasUnsavedChanges(), false);
    assert.equal(reusable.calls.length, 1, "Opening only reads metadata; it neither copies secrets nor saves a provider");
    for (const candidate of ["modelscope", "minimax", "siliconflow", "deepseek", "doubao", "custom"]) {
      assert.equal(reusable.get(`libraryAIKeyBlock-${candidate}`).hidden, candidate !== "deepseek", "Only the selected provider's key card is shown");
    }
    assert.equal(reusable.get("libraryAIReadingReuse").hidden, false);
    assert.equal(reusable.get("libraryAIUseReadingService").textContent, `使用已配置的${names[provider]}服务`);
    reusable.response((url, payload) => {
      assert.ok(url.endsWith("/share-reading-key"));
      assert.deepEqual(payload, { provider });
      return { ...previous, mode: "api", provider, base_url: "https://offline-read.example/v1", model: "saved-reading-model",
        supports_images: true, key_configured: true, keys: { [provider]: { configured: true, count: 1, shared_with_reading: true } } };
    });
    reusable.get("libraryAIUseReadingService").trigger("click"); await flush();
    assert.equal(reusable.calls.length, 2);
    assert.equal(reusable.get("libraryAIProvider").value, provider);
    assert.equal(reusable.get("libraryAIModel").value, "saved-reading-model");
    assert.equal(reusable.get(`libraryAIKeyBlock-${provider}`).hidden, false);
    assert.equal(reusable.get("libraryAIKeyBlock-deepseek").hidden, true);
    assert.equal(reusable.get("libraryAITags").checked, false); assert.equal(reusable.get("libraryAIAnswer").checked, false);
    assert.equal(reusable.get("libraryAITestConsent").checked, false, "Reuse does not consent to a paid test");
    assert.equal(reusable.calls.some(call => call.url.endsWith("/test")), false);
  }

  // 7. 主动复用先保护真正的本会话草稿；拒绝不会丢模型或新密钥。
  const protectedDraft = setup(); protectedDraft.setBody({ ...protectedDraft.getBody(), provider: "deepseek", model: "old-model" });
  protectedDraft.window.LibraryAISettings.setReadingKeys({ modelscope: { configured: true, count: 1 } });
  await protectedDraft.window.LibraryAISettings.open();
  protectedDraft.get("libraryAIKey-deepseek").value = "synthetic-unsaved-replacement";
  protectedDraft.get("libraryAIKey-deepseek").trigger("input");
  protectedDraft.consent(false);
  protectedDraft.get("libraryAIUseReadingService").trigger("click"); await flush();
  assert.equal(protectedDraft.calls.length, 1);
  assert.equal(protectedDraft.get("libraryAIProvider").value, "deepseek");
  assert.equal(protectedDraft.get("libraryAIKey-deepseek").value, "synthetic-unsaved-replacement");
  assert.equal(protectedDraft.window.LibraryAISettings.hasUnsavedChanges(), true);

  const pendingReuse = setup(); pendingReuse.window.LibraryAISettings.setReadingKeys({ minimax: { configured: true, count: 1 } });
  await pendingReuse.window.LibraryAISettings.open();
  let finishReuse;
  pendingReuse.response(() => new Promise(resolve => { finishReuse = resolve; }));
  pendingReuse.get("libraryAIShareReadingKey").trigger("click"); await flush();
  assert.equal(pendingReuse.window.LibraryAISettings.isMutating(), true);
  assert.equal(pendingReuse.window.LibraryAISettings.deactivate(), false); assert.equal(pendingReuse.window.LibraryAISettings.discard(), false);
  pendingReuse.get("libraryAIShareReadingKey").trigger("click"); await flush();
  assert.equal(pendingReuse.calls.filter(call => call.url.endsWith("/share-reading-key")).length, 1, "An in-flight copy is neither duplicated nor treated as cancelable metadata");
  finishReuse({ ...pendingReuse.getBody(), key_configured: true }); await flush();
  assert.equal(pendingReuse.window.LibraryAISettings.isMutating(), false);

  const staleDecision = setup(); staleDecision.setBody({ ...staleDecision.getBody(), provider: "deepseek" });
  staleDecision.window.LibraryAISettings.setReadingKeys({ modelscope: { configured: true, count: 1 } });
  await staleDecision.window.LibraryAISettings.open();
  staleDecision.get("libraryAIKey-deepseek").value = "old-session-synthetic-draft"; staleDecision.get("libraryAIKey-deepseek").trigger("input");
  let resolveDecision; staleDecision.consent(new Promise(resolve => { resolveDecision = resolve; }));
  staleDecision.get("libraryAIUseReadingService").trigger("click"); await flush();
  assert.equal(staleDecision.window.LibraryAISettings.discard(), true); assert.equal(staleDecision.window.LibraryAISettings.deactivate(), true);
  await staleDecision.window.LibraryAISettings.open();
  staleDecision.get("libraryAIKey-deepseek").value = "reopened-session-synthetic-draft"; staleDecision.get("libraryAIKey-deepseek").trigger("input");
  resolveDecision(true); await flush();
  assert.equal(staleDecision.calls.filter(call => call.url.endsWith("/share-reading-key")).length, 0, "A late accepted confirmation cannot mutate a reopened settings session");
  assert.equal(staleDecision.get("libraryAIKey-deepseek").value, "reopened-session-synthetic-draft");

  console.log("Three reading providers: explicit reuse, selected key card, preserved legacy configuration, safe independent-key entry, draft protection and mutation/session guards: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
