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
    constructor() {
      this.value = ""; this.checked = false; this.disabled = false; this.open = false;
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
    append() {}
    showModal() { this.open = true; }
    close() { this.open = false; this.trigger("close"); }
    trigger(name, detail = {}) { for (const fn of this.listeners.get(name) || []) fn({ preventDefault() {}, ...detail }); }
  }
  const document = { head: new Element(), body: new Element(), listeners: new Map(), events: [],
    createElement: () => new Element(), getElementById: (id) => elements.get(id),
    addEventListener(name, fn) { this.listeners.set(name, fn); },
    dispatchEvent(event) { this.events.push(event); } };
  const current = { configured: false, ready: false, endpoint_id: "", thinking: true,
    features: { knowledge_tags: false, ai_answer: false }, message: "生成暂停" };
  const calls = [];
  let respond = () => ({ ...current, features: { ...current.features } });
  let okay = true;
  let confirm = true;
  const window = { confirm: () => confirm };
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
  assert.equal(s.get("libraryAIKey").value, "");
  assert.equal(s.get("libraryAITest").disabled, true);
  assert.equal(s.calls.length, 1, "opening only reads non-secret settings");

  s.get("libraryAITags").checked = true;
  s.get("libraryAITags").trigger("input");
  assert.equal(s.get("libraryAIAnswer").checked, false, "enabling tags must not enable answers");
  s.get("libraryAIEndpoint").value = "ep-offline-pro";
  s.get("libraryAIKey").value = "offline-key";
  s.get("libraryAIKey").trigger("input");
  s.response((_url, payload) => ({ ...s.current, configured: true, endpoint_id: payload.endpoint_id, features: payload.features }));
  s.get("libraryAISettingsForm").trigger("submit");
  await flush();
  const saved = s.calls.at(-1);
  assert.equal(saved.url, "/api/settings/library-ai");
  assert.deepEqual(saved.payload.features, { knowledge_tags: true, ai_answer: false });
  assert.deepEqual(saved.payload.key, { action: "replace", value: "offline-key" });
  assert.equal(saved.payload.thinking, true);
  assert.equal(saved.headers["X-QB-Request"], "1");
  assert.equal(s.get("libraryAIKey").value, "", "replacement key is cleared after save");
  assert.equal(s.get("libraryAITest").disabled, true, "saving never starts or consents to a paid probe");
  assert.equal(s.document.events.at(-1).type, "library-ai-settings-saved");

  s.get("libraryAITest").trigger("click");
  await flush();
  assert.equal(s.calls.length, 2, "test handler also requires explicit consent");
  s.get("libraryAITestConsent").checked = true;
  s.get("libraryAITestConsent").trigger("change");
  assert.equal(s.get("libraryAITest").disabled, false);
  s.response(() => ({ ...s.current, configured: true, ready: true }));
  s.get("libraryAITest").trigger("click");
  await flush();
  assert.equal(s.calls.at(-1).url, "/api/settings/library-ai/test");
  assert.deepEqual(s.calls.at(-1).payload, { confirm: true });
  assert.equal(s.get("libraryAITestConsent").checked, false, "each probe needs fresh consent");

  s.get("libraryAIKey").value = "failed-offline-key";
  s.get("libraryAIKey").trigger("input");
  s.response(() => ({ error: "格式无效" }), false);
  s.get("libraryAISettingsForm").trigger("submit");
  await flush();
  assert.equal(s.get("libraryAIKey").value, "", "failed saves do not retain a password input");
  assert.equal(s.get("libraryAIResult").textContent, "格式无效");
  s.consentClose(false);
  s.get("libraryAICancel").trigger("click");
  assert.equal(s.get("libraryAISettingsDialog").open, true, "continue editing preserves dirty settings");
  s.consentClose(true);
  s.get("libraryAICancel").trigger("click");
  assert.equal(s.get("libraryAISettingsDialog").open, false);

  const pending = setup();
  let resolve;
  pending.response(() => new Promise((done) => { resolve = done; }));
  const opened = pending.window.LibraryAISettings.open();
  pending.get("libraryAICancel").trigger("click");
  resolve({ ...pending.current, configured: true, ready: true, endpoint_id: "ep-stale-pro" });
  await opened;
  assert.equal(pending.get("libraryAISettingsDialog").open, false, "old requests cannot reopen a closed window");
  assert.equal(pending.get("libraryAIKey").value, "");

  const broken = setup();
  broken.response(() => ({ ready: false }));
  await broken.window.LibraryAISettings.open();
  assert.equal(broken.get("libraryAISave").disabled, true, "an incomplete GET must not overwrite feature settings with defaults");
  assert.equal(broken.calls.length, 1);
  console.log("independent library AI dialog action checks: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
