"use strict";

// Exercise actual dialog actions in an isolated DOM/transport. No API service,
// password store or browser profile is used by this test.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function flush() { for (let i = 0; i < 12; i += 1) await Promise.resolve(); }

function setup() {
  const elements = new Map();
  class Element {
    constructor(tag = "div") {
      this.value = ""; this.checked = false; this.disabled = false; this.open = false;
      this.tagName = tag.toUpperCase(); this.children = []; this.modalOpens = 0;
      this.listeners = new Map(); this.classList = { toggle() {} };
    }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    set innerHTML(html) {
      for (const match of html.matchAll(/<[^>]+id="([^"]+)"[^>]*>/g)) {
        const child = new Element();
        child.disabled = /\sdisabled(?:\s|>)/.test(match[0]);
        elements.set(match[1], child);
      }
    }
    addEventListener(name, fn) { this.listeners.set(name, [...(this.listeners.get(name) || []), fn]); }
    setAttribute() {}
    append(child) { this.children.push(child); child.parentElement = this; }
    showModal() { this.open = true; this.modalOpens++; }
    close() { this.open = false; this.trigger("close"); }
    trigger(name, detail = {}) { for (const fn of this.listeners.get(name) || []) fn({ preventDefault() {}, ...detail }); }
  }
  const document = { head: new Element(), body: new Element(), listeners: new Map(), events: [],
    createElement: (tag) => new Element(tag), getElementById: (id) => elements.get(id),
    addEventListener(name, fn) { this.listeners.set(name, fn); },
    dispatchEvent(event) { this.events.push(event); } };
  const current = { mode: "assistant", provider: "deepseek", base_url: "https://api.deepseek.com", model: "deepseek-v4-pro",
    configured: false, key_configured: false, ready: true, api_ready: false, verified: false, supports_images: false, thinking: true,
    features: { knowledge_tags: false, ai_answer: false }, on_intake: { tags: false, answer: false }, message: "交给当前助手处理" };
  const calls = [];
  let respond = () => ({ ...current, features: { ...current.features } });
  let okay = true;
  let confirm = true;
  const window = { confirm: () => confirm, location: { hash: "" }, listeners: new Map(),
    addEventListener(name, fn) { this.listeners.set(name, fn); } };
  const sandbox = { window, document, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : undefined;
      calls.push({ url, payload, headers: options.headers });
      const body = await respond(url, payload);
      return { ok: okay, json: async () => body };
    } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "library-ai-settings.js"), "utf8"), sandbox);
  return { window, document, current, calls, get: (id) => elements.get(id),
    response: (fn, ok = true) => { respond = fn; okay = ok; }, consentClose: (value) => { confirm = value; } };
}

(async () => {
  const s = setup();
  await s.window.LibraryAISettings.open();
  assert.equal(s.get("libraryAITags").checked, false);
  assert.equal(s.get("libraryAIAnswer").checked, false);
  assert.equal(s.get("libraryAITagsIntake").checked, false, "intake generation is opt-in, separately from the feature");
  assert.equal(s.get("libraryAIAnswerIntake").checked, false);
  assert.equal(s.get("libraryAITagsIntake").disabled, true);
  assert.equal(s.get("libraryAIAnswerIntake").disabled, true);
  assert.equal(s.get("libraryAITagsTiming").hidden, true);
  assert.equal(s.get("libraryAIAnswerTiming").hidden, true);
  assert.equal(s.get("libraryAIMode").value, "assistant");
  assert.equal(s.get("libraryAIAdvanced").open, false, "default view hides optional API setup");
  assert.equal(s.get("libraryAIAPIFields").hidden, true);
  assert.equal(s.get("libraryAIAssistantHelp").hidden, false);
  assert.equal(s.get("libraryAIKey").value, "");
  assert.equal(s.get("libraryAITest").disabled, true);
  assert.equal(s.calls.length, 1, "opening only reads non-secret settings");

  s.get("libraryAITags").checked = true;
  s.get("libraryAITags").trigger("input");
  assert.equal(s.get("libraryAIAnswer").checked, false, "enabling tags must not enable answers");
  assert.equal(s.get("libraryAITagsIntake").disabled, false);
  assert.equal(s.get("libraryAITagsTiming").hidden, false);
  assert.equal(s.get("libraryAITagsIntake").checked, false, "enabling a feature keeps manual timing by default");
  assert.equal(s.get("libraryAIAnswerIntake").disabled, true);
  s.get("libraryAITagsIntake").checked = true;s.get("libraryAITagsIntake").trigger("input");
  s.get("libraryAITags").checked = false;s.get("libraryAITags").trigger("input");
  assert.equal(s.get("libraryAITagsIntake").checked, true, "turning a feature off preserves its saved timing preference");
  assert.equal(s.get("libraryAITagsIntake").disabled, true);
  assert.equal(s.get("libraryAITagsTiming").hidden, true);
  s.get("libraryAITags").checked = true;s.get("libraryAITags").trigger("input");
  assert.equal(s.get("libraryAITagsIntake").checked, true);
  // Even a stale password input must not be submitted by the default mode.
  s.get("libraryAIKey").value = "hidden-offline-key";
  s.response((_url, payload) => ({ ...s.current, mode: payload.mode, features: payload.features, on_intake: payload.on_intake }));
  s.get("libraryAISettingsForm").trigger("submit");
  await flush();
  assert.deepEqual(s.calls.at(-1).payload, { mode: "assistant", features: { knowledge_tags: true, ai_answer: false }, on_intake: { tags: true, answer: false } });
  assert.equal(s.calls.at(-1).headers["X-QB-Request"], "1");
  assert.equal(s.get("libraryAIKey").value, "", "assistant saves clear local password input without changing the stored key");
  assert.match(s.get("libraryAIResult").textContent, /当前助手/);
  assert.equal(s.document.events.at(-1).type, "library-ai-settings-saved");
  s.get("libraryAITestConsent").checked = true;
  s.get("libraryAITest").trigger("click");
  await flush();
  assert.equal(s.calls.length, 2, "assistant mode never probes an API, even with a forged consent event");

  s.get("libraryAIMode").value = "api";
  s.get("libraryAIMode").trigger("input");
  assert.equal(s.get("libraryAIAPIFields").hidden, false);
  assert.equal(s.get("libraryAIAssistantHelp").hidden, true);
  assert.equal(s.get("libraryAIImages").checked, false, "DeepSeek image support must not be assumed");
  assert.equal(s.get("libraryAIModel").value, "deepseek-v4-pro", "use a real model ID rather than the label Pro");
  s.get("libraryAIKey").value = "offline-api-key";
  s.get("libraryAIKey").trigger("input");
  s.response((_url, payload) => ({ ...s.current, ...payload, configured: true, ready: false, api_ready: false }));
  s.get("libraryAISettingsForm").trigger("submit");
  await flush();
  const saved = s.calls.at(-1);
  assert.equal(saved.url, "/api/settings/library-ai");
  assert.equal(saved.payload.mode, "api");
  assert.equal(saved.payload.provider, "deepseek");
  assert.equal(saved.payload.base_url, "https://api.deepseek.com");
  assert.equal(saved.payload.model, "deepseek-v4-pro");
  assert.equal(saved.payload.supports_images, false);
  assert.equal(saved.payload.thinking, true);
  assert.equal(saved.payload.reasoning_effort, "high");
  assert.deepEqual(saved.payload.features, { knowledge_tags: true, ai_answer: false });
  assert.deepEqual(saved.payload.on_intake, { tags: true, answer: false });
  assert.deepEqual(saved.payload.key, { action: "replace", value: "offline-api-key" });
  assert.equal(s.get("libraryAIKey").value, "", "replacement key is cleared after save");
  assert.equal(s.get("libraryAITest").disabled, true, "saving does not consent to or start a paid probe");
  s.get("libraryAITest").trigger("click");
  await flush();
  assert.equal(s.calls.length, 3, "test handler also requires fresh explicit consent");
  s.get("libraryAITestConsent").checked = true;
  s.get("libraryAITestConsent").trigger("change");
  assert.equal(s.get("libraryAITest").disabled, false);
  s.response(() => ({ ...s.current, mode: "api", configured: true, ready: true, api_ready: true, verified: true }));
  s.get("libraryAITest").trigger("click");
  await flush();
  assert.equal(s.calls.at(-1).url, "/api/settings/library-ai/test");
  assert.deepEqual(s.calls.at(-1).payload, { confirm: true });
  assert.equal(s.get("libraryAITestConsent").checked, false, "each probe requires fresh consent");

  s.get("libraryAIProvider").value = "custom";
  s.get("libraryAIKey").value = "previous-provider-unsaved-key";
  s.get("libraryAIProvider").trigger("change");
  assert.equal(s.get("libraryAIKey").value, "", "a provider switch cannot reuse a newly typed key for a different service");
  assert.equal(s.get("libraryAIBaseURL").value, "");
  assert.equal(s.get("libraryAIModel").value, "");
  assert.equal(s.get("libraryAIImages").checked, false);
  s.get("libraryAIBaseURL").value = "https://offline.example/v1";
  s.get("libraryAIModel").value = "offline-math-model";
  s.get("libraryAIImages").checked = true;
  s.get("libraryAIKey").value = "failed-offline-key";
  s.get("libraryAIKey").trigger("input");
  s.response(() => ({ error: "格式无效" }), false);
  s.get("libraryAISettingsForm").trigger("submit");
  await flush();
  assert.equal(s.calls.at(-1).payload.provider, "custom");
  assert.equal(s.calls.at(-1).payload.supports_images, true);
  assert.equal(s.get("libraryAIKey").value, "", "failed saves do not retain a password input");
  assert.match(s.get("libraryAIResult").textContent, /格式无效.*新密钥未保存.*重新填写/);
  assert.equal(s.get("libraryAISave").disabled, true, "a failed replacement cannot become a silent keep on retry");
  const failedSaveCount = s.calls.length;
  s.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.equal(s.calls.length, failedSaveCount, "retry without re-entering a failed replacement never posts keep");
  s.get("libraryAIKey").value = "retry-offline-key"; s.get("libraryAIKey").trigger("input");
  assert.equal(s.get("libraryAISave").disabled, false, "re-entering the key enables an explicit replacement retry");
  s.response((_url, payload) => ({ ...s.current, ...payload, configured: true, ready: false }));
  s.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.deepEqual(s.calls.at(-1).payload.key, { action: "replace", value: "retry-offline-key" });
  s.get("libraryAIBaseURL").value = "https://offline-new.example/v1"; s.get("libraryAIBaseURL").trigger("input");
  s.consentClose(false);
  s.get("libraryAICancel").trigger("click");
  assert.equal(s.get("libraryAISettingsDialog").open, true, "continue editing preserves dirty settings");
  s.consentClose(true);
  s.get("libraryAICancel").trigger("click");
  assert.equal(s.get("libraryAISettingsDialog").open, false);

  const legacy = setup();
  legacy.response((_url, payload) => ({ ...legacy.current, configured: true, provider: "doubao",
    base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "ep-existing-offline", features: payload?.features || legacy.current.features }));
  await legacy.window.LibraryAISettings.open();
  assert.equal(legacy.get("libraryAIAdvanced").open, false, "existing API settings stay optional in assistant mode");
  assert.equal(legacy.get("libraryAIModel").value, "ep-existing-offline");
  legacy.get("libraryAITags").checked = true;legacy.get("libraryAITags").trigger("input");
  legacy.get("libraryAISettingsForm").trigger("submit");await flush();
  assert.deepEqual(Object.keys(legacy.calls.at(-1).payload).sort(), ["features", "mode", "on_intake"], "assistant saves preserve hidden legacy API configuration and key");
  legacy.get("libraryAIMode").value = "api";legacy.get("libraryAIMode").trigger("input");
  legacy.get("libraryAIKey").value = "discard-local-value";
  legacy.get("libraryAIClearKey").checked = true;legacy.get("libraryAIClearKey").trigger("input");
  assert.equal(legacy.get("libraryAIKey").value, "");
  assert.equal(legacy.get("libraryAIKey").disabled, true);
  legacy.response((_url, payload) => ({ ...legacy.current, mode: "api", provider: "doubao", configured: false, ready: false, features: payload.features }));
  legacy.get("libraryAISettingsForm").trigger("submit");await flush();
  assert.deepEqual(legacy.calls.at(-1).payload.key, { action: "clear" });

  legacy.get("libraryAIClearKey").checked = true; legacy.get("libraryAIClearKey").trigger("input");
  legacy.response(() => ({ error: "暂时未保存" }), false);
  legacy.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.equal(legacy.get("libraryAIClearKey").checked, true, "failed deletion keeps the user's explicit delete intent");
  assert.equal(legacy.get("libraryAIKey").disabled, true);
  legacy.response((_url, payload) => ({ ...legacy.current, mode: "api", configured: false, ready: false, features: payload.features }));
  legacy.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.deepEqual(legacy.calls.at(-1).payload.key, { action: "clear" }, "retry still deletes instead of retaining an old key");

  const pending = setup();
  let resolve;
  pending.response(() => new Promise((done) => { resolve = done; }));
  const opened = pending.window.LibraryAISettings.open();
  pending.get("libraryAICancel").trigger("click");
  resolve({ ...pending.current, mode: "api", configured: true, ready: true });
  await opened;
  assert.equal(pending.get("libraryAISettingsDialog").open, false, "old requests cannot reopen a closed window");
  assert.equal(pending.get("libraryAIKey").value, "");

  const broken = setup();
  broken.response(() => ({ ready: false }));
  await broken.window.LibraryAISettings.open();
  assert.equal(broken.get("libraryAISave").disabled, true, "an incomplete GET must not overwrite feature settings with defaults");
  assert.equal(broken.calls.length, 1);
  const incompleteAPI = setup();
  incompleteAPI.response(() => ({ ...incompleteAPI.current, supports_images: undefined }));
  await incompleteAPI.window.LibraryAISettings.open();
  assert.equal(incompleteAPI.get("libraryAISave").disabled, true, "missing model capability metadata must not reset an existing API configuration");
  const incompleteTiming = setup();
  incompleteTiming.response(() => ({ ...incompleteTiming.current, on_intake: { tags: false } }));
  await incompleteTiming.window.LibraryAISettings.open();
  assert.equal(incompleteTiming.get("libraryAISave").disabled, true, "an incomplete timing response cannot silently reset intake preferences");

  const inline = setup();
  const host = inline.document.createElement("div");
  await inline.window.LibraryAISettings.mount(host);
  assert.equal(host.children.length, 1);
  assert.equal(inline.get("libraryAISettingsDialog").tagName, "SECTION");
  assert.equal(inline.get("libraryAISettingsDialog").modalOpens, 0, "the settings page never opens a nested modal");
  assert.equal(inline.calls.length, 1, "mounting only reads stored settings");
  assert.equal(inline.get("libraryAITags").checked, false);
  assert.equal(inline.get("libraryAIAnswer").checked, false);
  inline.get("libraryAITags").checked = true; inline.get("libraryAITags").trigger("input");
  assert.equal(inline.window.LibraryAISettings.hasUnsavedChanges(), true);
  await inline.window.LibraryAISettings.mount(host);
  assert.equal(inline.calls.length, 1, "revisiting the tab cannot overwrite an unsaved draft");
  assert.equal(inline.get("libraryAITags").checked, true);
  let prevented = false;
  const unload = { preventDefault() { prevented = true; }, returnValue: undefined };
  inline.window.listeners.get("beforeunload")(unload);
  assert.equal(prevented, true);
  assert.equal(unload.returnValue, "", "refresh/close protects unsaved inline settings");
  inline.get("libraryAIKey").value = "discard-unsaved-secret";
  assert.equal(inline.window.LibraryAISettings.discard(), true);
  assert.equal(inline.get("libraryAIKey").value, "");
  assert.equal(inline.get("libraryAITags").checked, false);
  assert.equal(inline.window.LibraryAISettings.hasUnsavedChanges(), false);
  assert.equal(inline.calls.length, 1, "discard restores the read snapshot without changing stored keys or flags");
  inline.get("libraryAITags").checked = true; inline.get("libraryAITags").trigger("input");
  let finishSave;
  inline.response((_url, payload) => new Promise((done) => { finishSave = () => done({ ...inline.current, features: payload.features, on_intake: payload.on_intake }); }));
  inline.get("libraryAISettingsForm").trigger("submit");
  assert.equal(inline.window.LibraryAISettings.isBusy(), true);
  assert.equal(inline.window.LibraryAISettings.discard(), false, "a running save cannot be silently discarded");
  finishSave(); await flush();
  assert.equal(inline.window.LibraryAISettings.isBusy(), false);
  assert.equal(inline.window.LibraryAISettings.hasUnsavedChanges(), false);
  assert.equal(inline.get("libraryAITags").checked, true);
  assert.deepEqual(inline.calls.at(-1).payload, { mode: "assistant", features: { knowledge_tags: true, ai_answer: false }, on_intake: { tags: false, answer: false } });

  const retry = setup();
  retry.response(() => ({ ready: false }));
  await retry.window.LibraryAISettings.mount(retry.document.createElement("div"));
  assert.equal(retry.get("libraryAISave").disabled, true);
  assert.equal(retry.get("libraryAICancel").textContent, "重新读取");
  retry.response(() => ({ ...retry.current }));
  retry.get("libraryAICancel").trigger("click"); await flush();
  assert.equal(retry.get("libraryAISave").disabled, false, "failed reads have a safe retry without saving defaults");
  assert.equal(retry.calls.length, 2);

  const minimax = setup();
  await minimax.window.LibraryAISettings.open();
  minimax.get("libraryAIKey").value = "unsaved-other-provider-key";
  minimax.get("libraryAIProvider").value = "minimax";
  minimax.get("libraryAIProvider").trigger("change");
  assert.equal(minimax.get("libraryAIKey").value, "", "MiniMax never adopts a typed key from another provider");
  assert.equal(minimax.get("libraryAIMode").value, "assistant", "selecting MiniMax cannot switch to a paid API");
  assert.equal(minimax.get("libraryAIBaseURL").value, "https://api.minimax.cn/v1");
  assert.equal(minimax.get("libraryAIModel").value, "MiniMax-M3.1-Flash-Preview");
  assert.equal(minimax.get("libraryAIImages").checked, true, "official M3.1 supports image_url input");
  assert.equal(minimax.get("libraryAIThinking").checked, true);
  assert.equal(minimax.get("libraryAIThinking").disabled, true, "M3.1 rejects disabled thinking at the API");
  assert.match(minimax.get("libraryAIMinimaxHelp").textContent, /M Plan.*不通用/);
  assert.equal(minimax.calls.length, 1, "a preset selection never tests or calls a model");
  minimax.get("libraryAIMode").value = "api"; minimax.get("libraryAIMode").trigger("change");
  assert.equal(minimax.get("libraryAIThinking").disabled, true);
  minimax.get("libraryAIModel").value = "MiniMax-M2.7"; minimax.get("libraryAIModel").trigger("input");
  assert.equal(minimax.get("libraryAIImages").checked, false);
  assert.equal(minimax.get("libraryAIImages").disabled, true, "M2 cannot claim image support");
  assert.equal(minimax.get("libraryAIThinking").checked, true);
  assert.equal(minimax.get("libraryAIThinking").disabled, true);
  assert.match(minimax.get("libraryAIMinimaxHelp").textContent, /仅支持文字/);
  minimax.get("libraryAIModel").value = "MiniMax-M3"; minimax.get("libraryAIModel").trigger("input");
  assert.equal(minimax.get("libraryAIImages").disabled, false);
  assert.equal(minimax.get("libraryAIThinking").disabled, false, "M3 permits disabled thinking");
  minimax.get("libraryAIThinking").checked = false; minimax.get("libraryAIThinking").trigger("input");
  assert.equal(minimax.get("libraryAIThinking").checked, false);
  minimax.get("libraryAIModel").value = "MiniMax-M3.1-Flash-Preview"; minimax.get("libraryAIModel").trigger("input");
  assert.equal(minimax.get("libraryAIThinking").checked, true, "returning to M3.1 restores required adaptive thinking");
  minimax.get("libraryAIImages").checked = true; minimax.get("libraryAIImages").trigger("input");
  minimax.get("libraryAIKey").value = "offline-explicit-minimax-key"; minimax.get("libraryAIKey").trigger("input");
  minimax.response((_url, payload) => ({ ...minimax.current, ...payload, configured: true, ready: false, api_ready: false }));
  minimax.get("libraryAISettingsForm").trigger("submit"); await flush();
  const selectedMinimax = minimax.calls.at(-1).payload;
  assert.equal(selectedMinimax.provider, "minimax");
  assert.equal(selectedMinimax.model, "MiniMax-M3.1-Flash-Preview");
  assert.equal(selectedMinimax.base_url, "https://api.minimax.cn/v1");
  assert.equal(selectedMinimax.supports_images, true);
  assert.equal(selectedMinimax.thinking, true);
  assert.equal(selectedMinimax.reasoning_effort, "high");
  assert.deepEqual(selectedMinimax.features, { knowledge_tags: false, ai_answer: false });
  assert.deepEqual(selectedMinimax.key, { action: "replace", value: "offline-explicit-minimax-key" });
  assert.equal(minimax.get("libraryAIKey").value, "");
  assert.equal(minimax.get("libraryAITest").disabled, true, "saving a subscription key does not start a test or generation");
  minimax.get("libraryAITest").trigger("click"); await flush();
  assert.equal(minimax.calls.length, 2);
  minimax.get("libraryAITestConsent").checked = true; minimax.get("libraryAITestConsent").trigger("change");
  minimax.response(() => ({ ...minimax.current, provider: "minimax", base_url: "https://api.minimax.cn/v1", model: "MiniMax-M3.1-Flash-Preview", supports_images: true, mode: "api", configured: true, ready: true, api_ready: true, verified: true }));
  minimax.get("libraryAITest").trigger("click"); await flush();
  assert.deepEqual(minimax.calls.at(-1).payload, { confirm: true });
  assert.equal(minimax.get("libraryAITestConsent").checked, false);
  console.log("assistant and optional API settings inline and dialog actions: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
