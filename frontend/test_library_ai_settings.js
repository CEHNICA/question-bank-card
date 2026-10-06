"use strict";

// Exercise actual dialog actions in an isolated DOM/transport. No API service,
// password store or browser profile is used by this test.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function flush() { for (let i = 0; i < 12; i += 1) await Promise.resolve(); }

function setup({ coordinator = true, ignoreAbort = false } = {}) {
  const elements = new Map();
  class Element {
    constructor(tag = "div") {
      this.value = ""; this.checked = false; this.disabled = false; this.open = false;
      this.tagName = tag.toUpperCase(); this.children = []; this.modalOpens = 0;
      this.attributes = {};
      this.listeners = new Map(); this.classList = { toggle() {}, add() {}, remove() {} };
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
    addEventListener(name, fn) { this.listeners.set(name, fn); },
    dispatchEvent(event) { this.events.push(event); } };
  const current = { mode: "assistant", provider: "deepseek", base_url: "https://api.deepseek.com", model: "deepseek-v4-pro",
    configured: false, key_configured: false, ready: true, api_ready: false, verified: false, supports_images: false, thinking: true,
    features: { knowledge_tags: false, ai_answer: false }, knowledge: { total: 73, chapters: 18, file: "C:/data/knowledge-points.txt" },
    on_intake: { tags: false, answer: false }, backlog: { tags: 12, answer: 5, total: 40 }, message: "交给当前助手处理" };
  const calls = [];
  let respond = () => ({ ...current, features: { ...current.features } });
  let okay = true;
  let confirm = true;
  const window = { confirm: () => confirm, location: { hash: "" }, listeners: new Map(),
    addEventListener(name, fn) { this.listeners.set(name, fn); } };
  const apiWindowCalls = [];
  if (coordinator) {
    const parent = new Element("dialog"), host = new Element("section"); parent.id = "apiSettingsDialog"; parent.append(host); document.body.append(parent);
    window.APISettings = {
      async open(tab) { apiWindowCalls.push(tab); if (!parent.open) parent.showModal(); await window.LibraryAISettings.mount(host, { embedded: true }); },
      close() {
        if (window.LibraryAISettings.isMutating()) return false;
        if (window.LibraryAISettings.hasUnsavedChanges()) {
          if (!window.confirm("API configuration has unsaved changes") || !window.LibraryAISettings.discard()) return false;
        }
        if (!window.LibraryAISettings.deactivate()) return false;
        parent.close(); return true;
      }
    };
  }
  const timers = new Map(); let nextTimer = 0;
  const sandbox = { window, document, AbortController, setTimeout: (fn, delay) => { const id = ++nextTimer; fn.delay = delay; timers.set(id, fn); return id; }, clearTimeout: (id) => timers.delete(id), CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : undefined;
      calls.push({ url, payload, headers: options.headers, signal: options.signal });
      const result = respond(url, payload);
      const body = await new Promise((resolve, reject) => {
        if (!ignoreAbort) options.signal?.addEventListener("abort", () => { const error = new Error("offline aborted request"); error.name = "AbortError"; reject(error); }, { once: true });
        Promise.resolve(result).then(resolve, reject);
      });
      return { ok: okay, json: async () => body };
    } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "library-ai-settings.js"), "utf8"), sandbox);
  // 每家服务商一栏，已保存的密钥是运行时按“已保存几条”生成的行，不在 id 表里。
  const rowOf = (handle, provider) => {
    const list = handle.get(`libraryAISaved-${provider}`);
    const item = list.children[0];
    return { list, item, number: item.children[0], value: item.children[1], eye: item.children[2] };
  };
  return { window, document, current, calls, timers, apiWindowCalls, rowOf, get: (id) => elements.get(id),
    response: (fn, ok = true) => { respond = fn; okay = ok; }, consentClose: (value) => { confirm = value; } };
}

(async () => {
  const s = setup();
  await s.window.LibraryAISettings.open();
  assert.deepEqual(s.apiWindowCalls, ["answers"], "The legacy entry delegates to the one parent API window's answer tab");
  assert.equal(s.get("libraryAISettingsDialog").tagName, "SECTION");
  assert.equal(s.get("libraryAISettingsDialog").modalOpens, 0, "The answer settings never open a second dialog");
  const routeOnly = setup({ coordinator: false }); await routeOnly.window.LibraryAISettings.open();
  assert.equal(routeOnly.window.location.href, "/settings#api"); assert.equal(routeOnly.calls.length, 0);
  assert.equal(routeOnly.get("libraryAISettingsDialog"), undefined, "A library-only page routes to the central API configuration instead of constructing another popup");

  const embedded = setup(); embedded.current.key_configured = true;
  await embedded.window.LibraryAISettings.open();
  assert.equal(embedded.get("libraryAIClose").hidden, true, "Only the outer API window owns its close control");
  embedded.get("libraryAITags").checked = true; embedded.get("libraryAITags").trigger("input");
  embedded.get("libraryAIKey-deepseek").value = "offline-embedded-unsaved-key"; embedded.get("libraryAIKey-deepseek").trigger("input");
  embedded.get("libraryAIKeyReveal-deepseek").trigger("click"); await flush();
  embedded.window.LibraryAISettings.hideSecrets();
  assert.equal(embedded.get("libraryAIKey-deepseek").value, "offline-embedded-unsaved-key"); assert.equal(embedded.get("libraryAIKey-deepseek").type, "password");
  assert.equal(embedded.window.LibraryAISettings.hasUnsavedChanges(), true, "Switching API tabs only hides secrets and preserves all real edits");
  const mountedReadCount = embedded.calls.length; await embedded.window.LibraryAISettings.open(); await embedded.window.LibraryAISettings.activate();
  assert.equal(embedded.calls.length, mountedReadCount); assert.equal(embedded.get("libraryAITags").checked, true);
  assert.equal(embedded.get("apiSettingsDialog").modalOpens, 1, "Repeated open delegates cannot reopen or replace the shared active window");
  assert.equal(embedded.window.LibraryAISettings.deactivate(), false, "The parent cannot deactivate an unconfirmed dirty answer panel");
  embedded.get("libraryAICancel").trigger("click");
  assert.equal(embedded.get("apiSettingsDialog").open, true, "The child undo button only restores its own group; it cannot close the shared API window");
  assert.equal(embedded.window.LibraryAISettings.hasUnsavedChanges(), false); assert.equal(embedded.get("libraryAIKey-deepseek").value, "");
  assert.equal(embedded.window.APISettings.close(), true); assert.equal(embedded.window.LibraryAISettings.isBusy(), false);
  const closedRequestCount = embedded.calls.length; embedded.get("libraryAISettingsForm").trigger("submit");
  await flush(); assert.equal(embedded.calls.length, closedRequestCount, "A deactivated panel cannot submit an old form");
  await embedded.window.LibraryAISettings.open();
  assert.equal(embedded.calls.length, closedRequestCount + 1, "Reopening the shared window refreshes metadata rather than retaining a stale profile");
  embedded.window.APISettings.close();

  const oldRead = setup({ ignoreAbort: true }); let finishOldRead;
  oldRead.response(() => new Promise(resolve => { finishOldRead = resolve; }));
  const oldOpen = oldRead.window.LibraryAISettings.open(); await flush();
  const oldSignal = oldRead.calls.at(-1).signal;
  assert.equal(oldRead.window.LibraryAISettings.isBusy(), true); assert.equal(oldRead.window.LibraryAISettings.isMutating(), false);
  assert.equal(oldRead.window.APISettings.close(), true, "Metadata reads must not trap the user in the central API window");
  assert(oldSignal.aborted); assert.equal(oldRead.window.LibraryAISettings.isBusy(), false);
  oldRead.response(() => ({ ...oldRead.current, features: { knowledge_tags: true, ai_answer: false } }));
  await oldRead.window.LibraryAISettings.open();
  oldRead.get("libraryAIModel").value = "offline-fresh-editor-model"; oldRead.get("libraryAIModel").trigger("input");
  finishOldRead({ ...oldRead.current }); await oldOpen;
  assert.equal(oldRead.get("apiSettingsDialog").open, true); assert.equal(oldRead.get("libraryAITags").checked, true);
  assert.equal(oldRead.get("libraryAIModel").value, "offline-fresh-editor-model"); assert.equal(oldRead.window.LibraryAISettings.hasUnsavedChanges(), true, "An ignored-abort old GET cannot reset a reopened draft");
  oldRead.window.APISettings.close();

  const pendingMutation = setup(); await pendingMutation.window.LibraryAISettings.open();
  pendingMutation.get("libraryAIAnswer").checked = true; pendingMutation.get("libraryAIAnswer").trigger("input");
  let completeMutation; pendingMutation.response((_url, payload) => new Promise(resolve => { completeMutation = () => resolve({ ...pendingMutation.current, ...payload }); }));
  pendingMutation.get("libraryAISettingsForm").trigger("submit"); await flush();
  const mutationSignal = pendingMutation.calls.at(-1).signal;
  assert.equal(pendingMutation.window.LibraryAISettings.isMutating(), true); assert.equal(pendingMutation.window.APISettings.close(), false);
  assert.equal(pendingMutation.window.LibraryAISettings.deactivate(), false); assert.equal(mutationSignal.aborted, false, "Close cannot pretend to undo an already-issued save");
  completeMutation(); await flush();
  assert.equal(pendingMutation.window.LibraryAISettings.isMutating(), false); assert.equal(pendingMutation.window.LibraryAISettings.hasUnsavedChanges(), false);
  assert.equal(pendingMutation.window.APISettings.close(), true);
  assert.equal(s.get("libraryAITags").checked, false);
  assert.equal(s.get("libraryAIAnswer").checked, false);
  assert.equal(s.get("libraryAITagsIntake").checked, false, "intake generation is opt-in, separately from the feature");
  assert.equal(s.get("libraryAIAnswerIntake").checked, false);
  assert.equal(s.get("libraryAITagsIntake").disabled, true);
  assert.equal(s.get("libraryAIAnswerIntake").disabled, true);
  assert.equal(s.get("libraryAITagsTiming").hidden, true);
  assert.equal(s.get("libraryAIAnswerTiming").hidden, true);
  assert.equal(s.get("libraryAIMode").value, "api");
  assert.equal(s.get("libraryAIAdvanced").open, true, "answer API setup is directly visible");
  assert.equal(s.get("libraryAIAPIFields").hidden, false);
  assert.equal(s.get("libraryAIAssistantHelp").hidden, true);
  assert.equal(s.get("libraryAIKey-deepseek").value, "");
  assert.equal(s.get("libraryAIKeyState-deepseek").textContent, "当前使用 · 未保存", "每一栏都写清自己有没有密钥，以及现在用的是哪一家");
  assert.equal(s.get("libraryAIKeyState-doubao").textContent, "未保存");
  assert.equal(s.get("libraryAIDelete-deepseek").hidden, true, "没有密钥时不给出删除动作");
  assert.equal(s.rowOf(s, "doubao").item.className, "credential-saved-empty");
  assert.equal(s.get("libraryAITest").disabled, true);
  assert.equal(s.calls.length, 1, "opening only reads non-secret settings");
  assert.equal(s.window.LibraryAISettings.hasUnsavedChanges(), false, "Showing the legacy assistant config as API-only does not manufacture a change");

  const unchanged = setup(); await unchanged.window.LibraryAISettings.mount(unchanged.document.createElement("section"));
  for (const id of ["libraryAITags", "libraryAIAnswer", "libraryAITagsIntake", "libraryAIAnswerIntake", "libraryAIImages", "libraryAIThinking", "libraryAIBaseURL", "libraryAIModel", "libraryAIKey-deepseek", "libraryAIKey-minimax", "libraryAIProvider", "libraryAIMode"]) {
    unchanged.get(id).trigger("focus"); unchanged.get(id).trigger("input"); unchanged.get(id).trigger("change");
    assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), false, `${id} without an effective change stays clean`);
  }
  unchanged.get("libraryAITestConsent").checked = true; unchanged.get("libraryAITestConsent").trigger("input"); unchanged.get("libraryAITestConsent").trigger("change");
  assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), false, "Test consent is transient permission, not a saved preference");
  unchanged.get("libraryAIKey-deepseek").value = " \n "; unchanged.get("libraryAIKey-deepseek").trigger("input");
  assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), false, "An empty replacement after trimming keeps the stored key");
  unchanged.get("libraryAIKey-deepseek").value = ""; unchanged.get("libraryAIKey-deepseek").trigger("input");
  for (const id of ["libraryAITags", "libraryAIAnswer", "libraryAITagsIntake", "libraryAIAnswerIntake", "libraryAIImages", "libraryAIThinking"]) {
    const field = unchanged.get(id), saved = field.checked;
    field.checked = !saved; field.trigger("input"); assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), true, `${id} real changes remain protected`);
    field.checked = saved; field.trigger("input"); assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), false, `${id} reverted to the snapshot is clean`);
  }
  for (const [id, edit] of [["libraryAIBaseURL", "https://offline.example/v1"], ["libraryAIModel", "offline-edited-model"], ["libraryAIKey-deepseek", "offline-new-key"], ["libraryAIKey-doubao", "offline-second-service-key"]]) {
    const field = unchanged.get(id), saved = field.value;
    field.value = edit; field.trigger("input"); assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), true);
    field.value = saved; field.trigger("input"); assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), false);
  }
  unchanged.get("libraryAIModel").value = ` ${unchanged.current.model} `; unchanged.get("libraryAIModel").trigger("input");
  assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), false, "Comparison uses the same trimmed model value as save");
  const noChangeUnload = { prevented: false, preventDefault() { this.prevented = true; } };
  unchanged.window.listeners.get("beforeunload")(noChangeUnload); assert.equal(noChangeUnload.prevented, false);
  unchanged.get("libraryAIKey-deepseek").value = "offline-unsaved-new-key"; unchanged.get("libraryAIKey-deepseek").trigger("input");
  unchanged.get("libraryAIProvider").trigger("change");
  assert.equal(unchanged.get("libraryAIKey-deepseek").value, "offline-unsaved-new-key", "A duplicate provider event must not discard an intentional replacement");
  assert.equal(unchanged.window.LibraryAISettings.hasUnsavedChanges(), true);
  unchanged.window.LibraryAISettings.discard();
  assert.equal(unchanged.get("libraryAIKey-doubao").value, "", "discard clears every service's pending replacement, not just the one in use");

  const restoredProvider = setup(); Object.assign(restoredProvider.current, { provider: "custom", base_url: "https://offline-custom.example/v1", model: "offline-saved-model", supports_images: true, thinking: false });
  await restoredProvider.window.LibraryAISettings.open();
  restoredProvider.get("libraryAIProvider").value = "minimax"; restoredProvider.get("libraryAIProvider").trigger("input"); restoredProvider.get("libraryAIProvider").trigger("change");
  assert.equal(restoredProvider.window.LibraryAISettings.hasUnsavedChanges(), true);
  restoredProvider.get("libraryAIProvider").value = "custom"; restoredProvider.get("libraryAIProvider").trigger("change");
  assert.equal(restoredProvider.get("libraryAIBaseURL").value, restoredProvider.current.base_url);
  assert.equal(restoredProvider.get("libraryAIModel").value, restoredProvider.current.model);
  assert.equal(restoredProvider.get("libraryAIImages").checked, true); assert.equal(restoredProvider.get("libraryAIThinking").checked, false);
  assert.equal(restoredProvider.window.LibraryAISettings.hasUnsavedChanges(), false, "Returning to the saved provider restores its real profile instead of an unsaved default");
  restoredProvider.consentClose(false); restoredProvider.window.APISettings.close();
  assert.equal(restoredProvider.get("apiSettingsDialog").open, false, "A clean close does not ask for discard confirmation");

  const switchStyle = unchanged.document.head.children[0].textContent, switchMarkup = unchanged.get("libraryAISettingsDialog").html;
  assert.match(switchStyle, /\.library-ai-switch\{[^}]*display:inline-flex;[^}]*width:fit-content;[^}]*max-width:100%;[^}]*justify-self:start;/);
  assert.match(switchMarkup, /<label class="library-ai-switch"><input id="libraryAITestConsent" type="checkbox"><span>/, "Checkbox and actual text retain native label/Space accessibility");

  s.get("libraryAITags").checked = true;
  s.get("libraryAITags").trigger("input");
  assert.equal(s.get("libraryAIAnswer").checked, false, "enabling tags must not enable answers");
  assert.equal(s.get("libraryAITagsIntake").disabled, false);
  assert.equal(s.get("libraryAITagsTiming").hidden, false);
  assert.equal(s.get("libraryAITagsIntake").checked, false, "enabling a feature keeps manual timing by default");
  assert.equal(s.get("libraryAIAnswerIntake").disabled, true);
  s.get("libraryAITagsIntake").checked = true;s.get("libraryAITagsIntake").trigger("input");
  s.get("libraryAITags").checked = false;s.get("libraryAITags").trigger("input");
  // 功能一关，入库时生成跟着收掉。原来是「保留用户的偏好」—— 但那一行这时候是藏着的，
  // 用户看不见自己留了开关；等他哪天把功能打开，每道新题入库就悄悄恢复调用一次服务。
  assert.equal(s.get("libraryAITagsIntake").checked, false, "turning a feature off closes its timing switch too");
  assert.equal(s.get("libraryAITagsIntake").disabled, true);
  assert.equal(s.get("libraryAITagsTiming").hidden, true);
  s.get("libraryAITags").checked = true;s.get("libraryAITags").trigger("input");
  assert.equal(s.get("libraryAITagsIntake").checked, false, "re-opening the feature does not resurrect it");
  s.response((_url, payload) => ({ ...s.current, mode: payload.mode, features: payload.features, on_intake: payload.on_intake }));
  s.get("libraryAISettingsForm").trigger("submit");
  await flush();
  assert.equal(s.calls.at(-1).payload.mode, "api", "only an explicit save changes legacy execution mode");
  assert.deepEqual(s.calls.at(-1).payload.features, { knowledge_tags: true, ai_answer: false });
// 上一步已经断言过「功能重新打开也不会自己复活入库时生成」，所以这里发出去的
// 必须是 tags:false —— 旧断言写的是 true，等于把「偷偷恢复调用」当成正确行为。
assert.deepEqual(s.calls.at(-1).payload.on_intake, { tags: false, answer: false });
  assert.deepEqual(s.calls.at(-1).payload.key, { action: "keep" });
  assert.equal(s.calls.at(-1).headers["X-QB-Request"], "1");
  assert.equal(s.get("libraryAIKey-deepseek").value, "", "assistant saves clear local password input without changing the stored key");
  assert.doesNotMatch(s.get("libraryAIResult").textContent, /当前助手/);
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
  s.get("libraryAIKey-deepseek").value = "offline-api-key";
  s.get("libraryAIKey-deepseek").trigger("input");
  s.get("libraryAIKey-doubao").value = "offline-doubao-key";
  s.get("libraryAIKey-doubao").trigger("input");
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
  assert.deepEqual(saved.payload.on_intake, { tags: false, answer: false });
  assert.deepEqual(saved.payload.key, { action: "replace", value: "offline-api-key" });
  assert.deepEqual(saved.payload.keys, { doubao: { action: "replace", value: "offline-doubao-key" } },
    "A second service's key is saved in the same submit, without switching which service is in use");
  assert.equal(s.get("libraryAIKey-deepseek").value, "", "replacement key is cleared after save");
  assert.equal(s.get("libraryAIKey-doubao").value, "");
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
  s.get("libraryAIKey-deepseek").value = "previous-provider-unsaved-key";
  s.get("libraryAIKey-deepseek").trigger("input");
  s.get("libraryAIProvider").trigger("change");
  assert.equal(s.get("libraryAIKey-custom").value, "", "a newly typed key stays in the service it was typed for");
  assert.equal(s.get("libraryAIKey-deepseek").value, "previous-provider-unsaved-key", "switching service does not throw away another service's pending key");
  assert.equal(s.get("libraryAIKeyState-custom").textContent, "当前使用 · 未保存");
  assert.equal(s.get("libraryAIKeyState-deepseek").textContent, "未保存", "the service that is no longer in use is still reported on its own");
  s.get("libraryAIBaseURL").value = "https://offline.example/v1";
  s.get("libraryAIModel").value = "offline-math-model";
  s.get("libraryAIImages").checked = true;
  s.get("libraryAIKey-custom").value = "failed-offline-key";
  s.get("libraryAIKey-custom").trigger("input");
  s.response(() => ({ error: "格式无效" }), false);
  s.get("libraryAISettingsForm").trigger("submit");
  await flush();
  assert.equal(s.calls.at(-1).payload.provider, "custom");
  assert.equal(s.calls.at(-1).payload.supports_images, true);
  assert.deepEqual(s.calls.at(-1).payload.keys, { deepseek: { action: "replace", value: "previous-provider-unsaved-key" } });
  assert.equal(s.get("libraryAIKey-custom").value, "", "failed saves do not retain a password input");
  assert.equal(s.get("libraryAIKey-deepseek").value, "", "a failed save also drops the other service's pending replacement");
  assert.match(s.get("libraryAIResult").textContent, /格式无效.*新密钥未保存.*重新填写/);
  assert.equal(s.get("libraryAISave").disabled, true, "a failed replacement cannot become a silent keep on retry");
  const failedSaveCount = s.calls.length;
  s.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.equal(s.calls.length, failedSaveCount, "retry without re-entering a failed replacement never posts keep");
  s.get("libraryAIKey-custom").value = "retry-offline-key"; s.get("libraryAIKey-custom").trigger("input");
  assert.equal(s.get("libraryAISave").disabled, true, "re-entering one service's key is not enough while another is still unconfirmed");
  s.get("libraryAIKey-deepseek").value = "retry-deepseek-key"; s.get("libraryAIKey-deepseek").trigger("input");
  assert.equal(s.get("libraryAISave").disabled, false, "re-entering the keys enables an explicit replacement retry");
  s.response((_url, payload) => ({ ...s.current, ...payload, configured: true, ready: false }));
  s.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.deepEqual(s.calls.at(-1).payload.key, { action: "replace", value: "retry-offline-key" });
  assert.deepEqual(s.calls.at(-1).payload.keys, { deepseek: { action: "replace", value: "retry-deepseek-key" } });
  s.get("libraryAIBaseURL").value = "https://offline-new.example/v1"; s.get("libraryAIBaseURL").trigger("input");
  s.consentClose(false);
  s.window.APISettings.close();
  assert.equal(s.get("apiSettingsDialog").open, true, "continue editing preserves dirty settings");
  s.consentClose(true);
  s.window.APISettings.close();
  assert.equal(s.get("apiSettingsDialog").open, false);

  const legacy = setup();
  legacy.response((_url, payload) => ({ ...legacy.current, configured: true, key_configured: true, key_count: 1, provider: "doubao",
    base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "ep-existing-offline", features: payload?.features || legacy.current.features }));
  await legacy.window.LibraryAISettings.open();
  assert.equal(legacy.get("libraryAIAdvanced").open, true, "existing API settings are directly visible without rewriting storage");
  assert.equal(legacy.get("libraryAIModel").value, "ep-existing-offline");
  legacy.get("libraryAITags").checked = true;legacy.get("libraryAITags").trigger("input");
  legacy.get("libraryAISettingsForm").trigger("submit");await flush();
  assert.equal(legacy.calls.at(-1).payload.model, "ep-existing-offline");
  assert.deepEqual(legacy.calls.at(-1).payload.key, { action: "keep" }, "explicit API save preserves the selected provider key");
  legacy.get("libraryAIMode").value = "api";legacy.get("libraryAIMode").trigger("input");
  assert.equal(legacy.get("libraryAIDelete-doubao").hidden, false, "a service with a saved key offers its own delete action");
  assert.equal(legacy.get("libraryAIDelete-deepseek").hidden, true, "a service without a saved key has no delete action");
  legacy.get("libraryAIKey-doubao").value = "discard-local-value";
  legacy.response((_url, payload) => ({ ...legacy.current, mode: "api", provider: "doubao", configured: false, ready: false, key_configured: false, features: payload?.features || legacy.current.features }));
  legacy.get("libraryAIDelete-doubao").trigger("click"); await flush();
  assert.deepEqual(legacy.calls.at(-1).payload.key, { action: "clear" }, "deleting the service in use clears exactly that one");
  assert.match(legacy.get("libraryAIResult").textContent, /已删除 豆包 密钥/);
  assert.equal(legacy.get("libraryAIKey-doubao").value, "");
  assert.equal(legacy.get("libraryAIDelete-doubao").hidden, true, "the delete action disappears once there is nothing left to delete");

  legacy.get("libraryAIKey-doubao").value = "restore-me";
  legacy.get("libraryAIKey-doubao").trigger("input");
  legacy.response(() => ({ error: "暂时未保存" }), false);
  legacy.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.match(legacy.get("libraryAIResult").textContent, /暂时未保存.*新密钥未保存.*重新填写/);
  assert.equal(legacy.get("libraryAIKey-doubao").value, "");
  assert.equal(legacy.get("libraryAISave").disabled, true, "an unconfirmed replacement blocks the next save instead of silently keeping the old key");
  legacy.get("libraryAIKey-doubao").value = "restore-me-again"; legacy.get("libraryAIKey-doubao").trigger("input");
  legacy.response((_url, payload) => ({ ...legacy.current, mode: "api", configured: true, ready: false, features: payload.features }));
  legacy.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.deepEqual(legacy.calls.at(-1).payload.key, { action: "replace", value: "restore-me-again" }, "retry replaces instead of quietly keeping the old key");

  const pending = setup();
  let resolve;
  pending.response(() => new Promise((done) => { resolve = done; }));
  const opened = pending.window.LibraryAISettings.open();
  pending.window.APISettings.close();
  resolve({ ...pending.current, mode: "api", configured: true, ready: true });
  await opened;
  assert.equal(pending.get("apiSettingsDialog").open, false, "old requests cannot reopen a closed window");
  assert.equal(pending.get("libraryAIKey-deepseek").value, "");

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
  inline.get("libraryAIKey-deepseek").value = "discard-unsaved-secret";
  assert.equal(inline.window.LibraryAISettings.discard(), true);
  assert.equal(inline.get("libraryAIKey-deepseek").value, "");
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
  assert.equal(inline.calls.at(-1).payload.mode, "api");
  assert.deepEqual(inline.calls.at(-1).payload.features, { knowledge_tags: true, ai_answer: false });
  assert.deepEqual(inline.calls.at(-1).payload.on_intake, { tags: false, answer: false });

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
  minimax.get("libraryAIKey-deepseek").value = "unsaved-other-provider-key";
  minimax.get("libraryAIKey-deepseek").trigger("input");
  minimax.get("libraryAIProvider").value = "minimax";
  minimax.get("libraryAIProvider").trigger("change");
  assert.equal(minimax.get("libraryAIKey-minimax").value, "", "MiniMax never adopts a typed key from another provider");
  assert.equal(minimax.get("libraryAIKey-deepseek").value, "unsaved-other-provider-key", "and the other provider keeps its own pending key");
  assert.equal(minimax.current.mode, "assistant", "selecting a preset never modifies the stored execution mode");
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
  minimax.get("libraryAIKey-minimax").value = "offline-explicit-minimax-key"; minimax.get("libraryAIKey-minimax").trigger("input");
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
  assert.deepEqual(selectedMinimax.keys, { deepseek: { action: "replace", value: "unsaved-other-provider-key" } },
    "the other service's pending key is saved too instead of being silently dropped");
  assert.equal(minimax.get("libraryAIKey-minimax").value, "");
  assert.equal(minimax.get("libraryAITest").disabled, true, "saving a subscription key does not start a test or generation");
  minimax.get("libraryAITest").trigger("click"); await flush();
  assert.equal(minimax.calls.length, 2);
  minimax.get("libraryAITestConsent").checked = true; minimax.get("libraryAITestConsent").trigger("change");
  minimax.response(() => ({ ...minimax.current, provider: "minimax", base_url: "https://api.minimax.cn/v1", model: "MiniMax-M3.1-Flash-Preview", supports_images: true, mode: "api", configured: true, ready: true, api_ready: true, verified: true }));
  minimax.get("libraryAITest").trigger("click"); await flush();
  assert.deepEqual(minimax.calls.at(-1).payload, { confirm: true });
  assert.equal(minimax.get("libraryAITestConsent").checked, false);
  const eye = setup();
  eye.current.key_configured = true; eye.current.configured = true;
  await eye.window.LibraryAISettings.open();
  assert.equal(eye.calls.length, 1, "opening reads metadata, never decrypts the saved key");
  assert.equal(eye.get("libraryAIKey-deepseek").type, "password");
  assert.equal(eye.get("libraryAIKeyState-deepseek").textContent, "当前使用 · 已保存 1 个");
  assert.equal(eye.rowOf(eye, "deepseek").value.textContent, "****************", "a saved key is listed masked, next to the service it belongs to");
  eye.response(() => ({ provider: "deepseek", key: "offline-reveal-test-only" }));
  eye.rowOf(eye, "deepseek").eye.trigger("click"); await flush();
  assert.equal(eye.calls.at(-1).url, "/api/settings/library-ai/key/reveal");
  assert.deepEqual(eye.calls.at(-1).payload, { provider: "deepseek", index: 0 });
  assert.equal(eye.rowOf(eye, "deepseek").value.textContent, "offline-reveal-test-only");
  assert.match(eye.get("libraryAIKeyNote").textContent, /正在查看 DeepSeek 已保存的密钥/);
  assert.equal(eye.get("libraryAIKey-deepseek").value, "", "Stored viewing never lands in the pending replacement input");
  assert.equal(eye.window.LibraryAISettings.hasUnsavedChanges(), false, "viewing is not a settings change");
  eye.response((_url, payload) => ({ ...eye.current, ...payload }));
  eye.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.deepEqual(eye.calls.at(-1).payload.key, { action: "keep" }, "visible saved key is never resubmitted as a replacement");
  assert.equal(eye.get("libraryAIKey-deepseek").value, "");
  assert.equal(eye.get("libraryAIKey-deepseek").type, "password");
  assert.equal(eye.rowOf(eye, "deepseek").value.textContent, "****************", "saving hides the revealed key again");
  eye.get("libraryAIKey-deepseek").value = "typed-offline-replacement"; eye.get("libraryAIKey-deepseek").trigger("input");
  const count = eye.calls.length;
  eye.get("libraryAIKeyReveal-deepseek").trigger("click"); await flush();
  assert.equal(eye.calls.length, count, "a typed key is shown locally without retrieving the saved key");
  assert.equal(eye.get("libraryAIKey-deepseek").type, "text");
  eye.get("libraryAIKeyReveal-deepseek").trigger("click");
  assert.equal(eye.get("libraryAIKey-deepseek").value, "typed-offline-replacement", "hiding a typed replacement preserves it for explicit save");
  assert.equal(eye.get("libraryAIKey-deepseek").type, "password");
  eye.get("libraryAICancel").trigger("click");
  assert.equal(eye.get("libraryAIKey-deepseek").value, "");

  const late = setup(); late.current.key_configured = true;
  await late.window.LibraryAISettings.open();
  let resolveKey; late.response(() => new Promise(done => { resolveKey = done; }));
  late.rowOf(late, "deepseek").eye.trigger("click"); await flush();
  late.get("libraryAIProvider").value = "minimax"; late.get("libraryAIProvider").trigger("change");
  resolveKey({ provider: "deepseek", key: "offline-late-secret" }); await flush();
  assert.equal(late.rowOf(late, "deepseek").value.textContent, "****************", "late reveals cannot expose a key after the teacher moved to another service");
  assert.equal(late.get("libraryAIKeyState-minimax").textContent, "当前使用 · 未保存");

  for (const action of ["cancel", "close", "typing", "visibility", "hash", "save"]) {
    const h = setup(); h.current.key_configured = true; h.current.configured = true;
    if (action === "hash") await h.window.LibraryAISettings.mount(h.document.createElement("section")); else await h.window.LibraryAISettings.open();
    let finish; h.response(() => new Promise(resolve => { finish = resolve; })); h.rowOf(h, "deepseek").eye.trigger("click"); await flush();
    const pendingKey = h.calls.at(-1); assert.equal(h.window.LibraryAISettings.isBusy(), false, "Key viewing does not lock settings busy");
    if (action === "cancel") h.rowOf(h, "deepseek").eye.trigger("click");
    if (action === "close") h.window.APISettings.close();
    if (action === "typing") { h.get("libraryAIKey-deepseek").value = "offline-new-input"; h.get("libraryAIKey-deepseek").trigger("input"); }
    if (action === "visibility") { h.document.hidden = true; h.document.listeners.get("visibilitychange")(); }
    if (action === "hash") { h.window.location.hash = "#general"; h.window.listeners.get("hashchange")(); }
    if (action === "save") {
      h.response((_url, payload) => ({ ...h.current, mode: payload.mode, features: payload.features, on_intake: payload.on_intake }));
      h.get("libraryAISettingsForm").trigger("submit"); await flush();
      assert.deepEqual(h.calls.at(-1).payload.key, { action: "keep" }, "Saving during pending viewing cannot submit a stored key replacement");
    }
    assert.equal(pendingKey.signal.aborted, true, `${action} cancels its pending reveal`);
    finish({ provider: "deepseek", key: "offline-obsolete-key" }); await flush();
    assert.equal(h.get("libraryAIKey-deepseek").value, action === "typing" ? "offline-new-input" : "", `${action} rejects a late revealed key`);
    assert.equal(h.get("libraryAIKey-deepseek").type, "password"); assert.equal(h.window.LibraryAISettings.isBusy(), false);
    assert.equal(h.rowOf(h, "deepseek").value.textContent, "****************", `${action} rejects a late stored value`);
  }
  const expires = setup(); expires.current.key_configured = true; await expires.window.LibraryAISettings.open();
  expires.response(() => ({ provider: "deepseek", key: "offline-expiring-key" })); expires.rowOf(expires, "deepseek").eye.trigger("click"); await flush();
  assert.equal(expires.timers.size, 1); assert.equal([...expires.timers.values()][0].delay, 60000); [...expires.timers.values()][0]();
  assert.equal(expires.get("libraryAIKey-deepseek").value, ""); assert.equal(expires.get("libraryAIKey-deepseek").type, "password");
  assert.equal(expires.rowOf(expires, "deepseek").value.textContent, "****************");
  assert.match(expires.get("libraryAIKeyNote").textContent, /点眼睛可查看 60 秒/, "the line explains itself again once nothing is being shown");
  expires.get("libraryAIKey-deepseek").value = "offline-typed-expiring-key"; expires.get("libraryAIKey-deepseek").trigger("input");
  expires.get("libraryAIKeyReveal-deepseek").trigger("click"); await flush();
  [...expires.timers.values()][0](); assert.equal(expires.get("libraryAIKey-deepseek").value, "offline-typed-expiring-key", "Expiry hides a typed key without deleting the intended replacement");
  assert.equal(expires.get("libraryAIKey-deepseek").type, "password");

  const profiles = setup(); Object.assign(profiles.current, { mode: "api", key_configured: true, key_count: 1, configured: true, ready: true,
    keys: { deepseek: { configured: true, count: 1 }, minimax: { configured: true, count: 1 }, doubao: { configured: false, count: 0 }, custom: { configured: true, count: 1 } } });
  await profiles.window.LibraryAISettings.open();
  assert.equal(profiles.calls.length, 1, "Listing all saved providers reads metadata without decrypting any key");
  const names = { deepseek: "DeepSeek", minimax: "MiniMax", custom: "其他兼容服务" };
  for (const provider of ["deepseek", "minimax", "custom"]) {
    assert.equal(profiles.get(`libraryAIKeyState-${provider}`).textContent, `${provider === "deepseek" ? "当前使用 · " : ""}已保存 1 个`);
    const row = profiles.rowOf(profiles, provider);
    assert.equal(row.value.textContent, "****************");
    assert.equal(row.value.className, "credential-saved-value", "a revealed key is marked so it cannot pass for a masked one");
    assert.equal(row.eye.getAttribute("aria-label"), `查看${names[provider]}第 1 条已保存的密钥`);
    assert.equal(profiles.get(`libraryAIDelete-${provider}`).hidden, false);
  }
  assert.equal(profiles.get("libraryAIKeyState-doubao").textContent, "未保存");
  assert.equal(profiles.rowOf(profiles, "doubao").item.className, "credential-saved-empty");
  assert.equal(profiles.rowOf(profiles, "doubao").eye, undefined, "an unconfigured service has no eye and no stored value to show");
  assert.equal(profiles.get("libraryAIDelete-doubao").hidden, true);
  assert.match(profiles.get("libraryAISettingsDialog").html, /id="libraryAISaved-minimax"[^>]*>/, "each service keeps its own saved-key list instead of one shared table");
  profiles.response((_url, payload) => ({ provider: payload.provider, index: payload.index, key: "offline-other-provider-stored-key" }));
  profiles.rowOf(profiles, "minimax").eye.trigger("click"); await flush();
  assert.deepEqual(profiles.calls.at(-1).payload, { provider: "minimax", index: 0 });
  assert.equal(profiles.get("libraryAIProvider").value, "deepseek", "Viewing a different service does not switch the API in use");
  assert.equal(profiles.rowOf(profiles, "minimax").value.textContent, "offline-other-provider-stored-key");
  assert.equal(profiles.rowOf(profiles, "minimax").eye.getAttribute("aria-label"), "隐藏MiniMax第 1 条已保存的密钥");
  assert.equal(profiles.get("libraryAIKey-minimax").value, ""); assert.equal(profiles.window.LibraryAISettings.hasUnsavedChanges(), false);
  profiles.rowOf(profiles, "minimax").eye.trigger("click"); assert.equal(profiles.rowOf(profiles, "minimax").value.textContent, "****************");
  profiles.get("libraryAIKey-deepseek").value = "offline-pending-active-replacement"; profiles.get("libraryAIKey-deepseek").trigger("input");
  profiles.response((_url, payload) => ({ provider: payload.provider, index: payload.index, key: "offline-other-provider-stored-key" }));
  profiles.rowOf(profiles, "custom").eye.trigger("click"); await flush();
  assert.equal(profiles.get("libraryAIKey-deepseek").value, "offline-pending-active-replacement", "Viewing another service does not overwrite a pending replacement");
  assert.equal(profiles.window.LibraryAISettings.hasUnsavedChanges(), true);
  profiles.response((_url, payload) => ({ ...profiles.current, ...payload })); profiles.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.deepEqual(profiles.calls.at(-1).payload.key, { action: "replace", value: "offline-pending-active-replacement" });
  assert.equal(profiles.calls.at(-1).payload.provider, "deepseek");
  for (const provider of ["deepseek", "minimax", "custom"]) assert.equal(profiles.rowOf(profiles, provider).value.textContent, "****************", "Saving clears every revealed key");
  assert.equal(profiles.window.LibraryAISettings.hasUnsavedChanges(), false);
  profiles.response((_url, payload) => ({ provider: payload.provider, index: payload.index, key: "offline-other-provider-stored-key" }));
  profiles.rowOf(profiles, "minimax").eye.trigger("click"); await flush();
  assert.equal(profiles.window.LibraryAISettings.hasUnsavedChanges(), false); [...profiles.timers.values()][0]();
  assert.equal(profiles.rowOf(profiles, "minimax").value.textContent, "****************"); assert.equal(profiles.window.LibraryAISettings.hasUnsavedChanges(), false, "Automatic hiding never manufactures pending edits");
  for (const mismatched of [{ provider: "custom", index: 0 }, { provider: "minimax", index: 1 }]) {
    profiles.response(() => ({ ...mismatched, key: "offline-mismatched-response" }));
    profiles.rowOf(profiles, "minimax").eye.trigger("click"); await flush();
    assert.equal(profiles.rowOf(profiles, "minimax").value.textContent, "****************", "Foreign-service and wrong-index reveal responses are never displayed");
    assert.equal(profiles.get("libraryAIKey-minimax").value, ""); assert.equal(profiles.window.LibraryAISettings.hasUnsavedChanges(), false);
  }
  const noStoredCalls = profiles.calls.length; profiles.rowOf(profiles, "doubao").item.trigger("click"); await flush();
  assert.equal(profiles.calls.length, noStoredCalls, "An unconfigured service cannot request a secret even with a forged click");

  // One service's delete never touches another, and never switches which one is in use.
  const deleters = setup(); Object.assign(deleters.current, { mode: "api", provider: "deepseek", configured: true, ready: true, key_configured: true,
    keys: { deepseek: { configured: true, count: 1 }, minimax: { configured: true, count: 1 }, doubao: { configured: false, count: 0 }, custom: { configured: false, count: 0 } } });
  await deleters.window.LibraryAISettings.open();
  deleters.response(() => ({ ...deleters.current }));
  deleters.get("libraryAIDelete-minimax").trigger("click"); await flush();
  assert.deepEqual(deleters.calls.at(-1).payload, { keys: { minimax: { action: "clear" } } },
    "Deleting a service that is not in use does not switch the API and does not clear the one in use");
  assert.match(deleters.get("libraryAIResult").textContent, /已删除 MiniMax 密钥，其他服务保持原样/);
  deleters.get("libraryAIDelete-deepseek").trigger("click"); await flush();
  assert.deepEqual(deleters.calls.at(-1).payload, { key: { action: "clear" } });

  const normalized = setup(); Object.assign(normalized.current, { provider: "minimax", base_url: "https://api.minimax.cn/v1", model: "MiniMax-M2.7", supports_images: true, thinking: false });
  await normalized.window.LibraryAISettings.open();
  assert.equal(normalized.get("libraryAIImages").checked, false); assert.equal(normalized.get("libraryAIThinking").checked, true);
  assert.equal(normalized.window.LibraryAISettings.hasUnsavedChanges(), false, "Rendering forced capabilities or hidden defaults does not falsely warn about user edits");

  const readTimeout = setup(); let finishRead;
  readTimeout.response(() => new Promise(resolve => { finishRead = resolve; })); const reading = readTimeout.window.LibraryAISettings.open(); await flush();
  assert.equal([...readTimeout.timers.values()][0].delay, 15000); [...readTimeout.timers.values()][0](); await reading;
  assert.equal(readTimeout.window.LibraryAISettings.isBusy(), false); assert.equal(readTimeout.get("libraryAIClose").disabled, false);
  assert.match(readTimeout.get("libraryAIState").textContent, /读取超时/); finishRead({ ...readTimeout.current }); await flush();
  assert.equal(readTimeout.get("libraryAISave").disabled, true, "A late cancelled read cannot install settings");

  const saveTimeout = setup(); await saveTimeout.window.LibraryAISettings.open();
  saveTimeout.get("libraryAIKey-deepseek").value = "offline-pending-save"; saveTimeout.get("libraryAIKey-deepseek").trigger("input");
  let finishTimedSave; saveTimeout.response(() => new Promise(resolve => { finishTimedSave = resolve; })); saveTimeout.get("libraryAISettingsForm").trigger("submit"); await flush();
  assert.equal([...saveTimeout.timers.values()][0].delay, 15000); [...saveTimeout.timers.values()][0](); await flush();
  assert.equal(saveTimeout.window.LibraryAISettings.isBusy(), false); assert.equal(saveTimeout.calls.length, 2, "Uncertain saves are not resubmitted");
  assert.match(saveTimeout.get("libraryAIResult").textContent, /结果.*未确认/); assert.equal(saveTimeout.get("libraryAIKey-deepseek").value, "");
  assert.equal(saveTimeout.get("libraryAISave").disabled, true, "A timeout requires replacing or rereading the unconfirmed key before another save");
  finishTimedSave({ ...saveTimeout.current }); await flush(); assert.equal(saveTimeout.window.LibraryAISettings.hasUnsavedChanges(), true);

  const testTimeout = setup(); Object.assign(testTimeout.current, { mode: "api", configured: true, key_configured: true, ready: true, api_ready: true });
  await testTimeout.window.LibraryAISettings.open(); testTimeout.get("libraryAITestConsent").checked = true; testTimeout.get("libraryAITestConsent").trigger("change");
  let finishTimedTest; testTimeout.response(url => url.endsWith("/test") ? new Promise(resolve => { finishTimedTest = resolve; }) : { ...testTimeout.current });
  testTimeout.get("libraryAITest").trigger("click"); await flush(); assert.equal([...testTimeout.timers.values()][0].delay, 420000); [...testTimeout.timers.values()][0](); await flush();
  assert.equal(testTimeout.window.LibraryAISettings.isBusy(), false); assert.equal(testTimeout.get("libraryAITestConsent").checked, false);
  assert.equal(testTimeout.calls.filter(call => call.url.endsWith("/test")).length, 1, "A bounded explicit test never automatically performs another test");
  assert.match(testTimeout.get("libraryAIResult").textContent, /结果.*未确认/); assert.equal(testTimeout.timers.size, 0);
  finishTimedTest({ ...testTimeout.current }); await flush(); assert.equal(testTimeout.calls.filter(call => call.url.endsWith("/test")).length, 1);
  console.log("API settings, explicit eye reveal and secret lifecycle: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
