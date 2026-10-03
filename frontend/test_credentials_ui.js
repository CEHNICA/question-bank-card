"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const dom = require("./credential-test-dom.js");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

assert.match(html, /id="settingsCredentialOpen"[^>]*>API 配置<\/button>/);
assert.match(html, /id="credentialDialog"[^>]*aria-labelledby="credentialTitle"/);
assert.doesNotMatch(html, /从开始菜单[^<]*配置 API/);

for (const provider of ["Mineru", "Modelscope", "Minimax", "Siliconflow"]) {
  assert.match(html, new RegExp(`id="credential${provider}Input"[^>]*type="password"`));
  assert.match(html, new RegExp(`id="credential${provider}Input"[^>]*autocomplete="off"[^>]*autocapitalize="off"[^>]*autocorrect="off"`));
  assert.match(html, new RegExp(`id="credential${provider}Delete"[^>]*type="button"[^>]*hidden>删除密钥</button>`));
  assert.match(html, new RegExp(`id="credential${provider}State"`));
  assert.match(html, new RegExp(`id="credential${provider}Saved"[^>]*class="credential-saved-list"`));
}
const aiSettings = fs.readFileSync(path.join(__dirname, "library-ai-settings.js"), "utf8");
assert.match(aiSettings, /id="libraryAIKey"[^>]*type="password"[^>]*autocomplete="off"/);
assert.doesNotMatch(aiSettings, /id="libraryAIKey"[^>]*autocomplete="(?:current|new)-password"/);

assert.match(js, /api\("\/api\/settings\/credentials"\)/);
assert.match(js, /api\("\/api\/settings\/credentials",\s*\{\s*method:\s*"POST"/);
assert.match(js, /action:\s*"replace",\s*accounts/);
assert.match(js, /action:\s*"keep"/);
assert.match(js, /action:\s*"clear"/);
assert.match(js, /resetCredentialInputs\(\);[\s\S]*?renderCredentialStates\(result\)/);
assert.match(js, /async function loadStatus\(\)[\s\S]*?return false;[\s\S]*?return true;/);
assert.match(js, /await refreshCredentialStatus\(refreshMessage, session\)/);
assert.match(js, /function openSettings\(\)[\s\S]*?showSettingsTab\(settingsTabFromHash\(\), \{ updateHash: false \}\);[\s\S]*?void loadStatus\(\);/);
assert.match(html, /已保存的密钥逐条隐藏显示，点眼睛可查看 60 秒/);
assert.match(html, /id="credentialSavedTotal"/);
assert.match(html, /不写入题库、日志或项目文件/);
assert.match(html, /不上传文件、不消耗识读额度/);
assert.match(html, />保存密钥<\/button>/);
assert.match(html, /class="credential-storage-note"/);
assert.doesNotMatch(html + js, /credential(?:Mineru|Modelscope|Minimax|Siliconflow)Clear|credential-clear|field\.clear/);
assert.doesNotMatch(html, /不消耗额度的官网验证/);

// 密钥申请有直接入口；MinerU 当前文档没有统一的14天到期承诺。
assert.match(html, /for="credentialMineruInput">[^<]*<a href="https:\/\/mineru\.net\/apiManage\/token"[^>]*>生成 Token<\/a>/);
assert.doesNotMatch(html, /14 天过期一次|免费，每天 1000 页/);
assert.match(html, /for="credentialModelscopeInput">[^<]*<a href="https:\/\/www\.modelscope\.cn\/my\/myaccesstoken"/);
for (const service of ["mineru", "modelscope", "minimax", "siliconflow"]) {
  assert.match(js, new RegExp(`const CREDENTIAL_FIELDS = \\{[\\s\\S]*?${service}: \\{ input: "credential`));
}
// 免费的魔搭排在付费服务前面；智谱已经去掉（免费模型高峰期常拒绝，实测不可用）。
assert.ok(html.indexOf('id="credentialModelscopeInput"') < html.indexOf('id="credentialMinimaxInput"'));
assert.doesNotMatch(html + js, /zhipu|智谱|bigmodel/i);

const functions = js.slice(js.indexOf("  const CREDENTIAL_FIELDS = {"), js.indexOf("  function renderSettingsTask("));
const listenersStart = js.indexOf('  Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {', js.indexOf('  $("selectionReread").addEventListener'));
const listeners = js.slice(listenersStart, js.indexOf("  // Queue the values captured", listenersStart));
assert.ok(functions && listenersStart > 0);
const services = ["mineru", "modelscope", "minimax", "siliconflow"];

function scenario() {
  const nodes = new Map();
  const requests = [], confirmations = [], messages = [];
  const saved = Object.fromEntries(services.map((service) => [service, { configured: true, count: 1 }]));
  const closeButtons = [{ disabled: false }, { disabled: false }];
  function $(id) {
    if (!nodes.has(id)) nodes.set(id, { ...dom.node(id), open: id === "credentialDialog",
      addEventListener(type, fn) { (this.events[type] ||= []).push(fn); },
      querySelectorAll() { return closeButtons; }, showModal() { this.open = true; }, focus() {} });
    return nodes.get(id);
  }
  const context = vm.createContext({ $, console, el: dom.el, icon: dom.icon, AbortController, setTimeout, clearTimeout,
    document: { addEventListener() {} }, window: { addEventListener() {} }, requestAnimationFrame(fn) { fn(); },
    toast(message, kind) { messages.push({ message, kind }); },
    confirmDialog: async (options) => { confirmations.push(options); return true; },
    loadStatus: async () => true,
    api: async (url, options) => {
      requests.push({ url, ...(options || {}) });
      if (options?.body?.services) {
        Object.entries(options.body.services).forEach(([service, operation]) => {
          if (operation.action === "clear") saved[service] = { configured: false, count: 0 };
          else if (operation.action === "replace") saved[service] = { configured: true, count: operation.accounts.length };
        });
      }
      return { services: JSON.parse(JSON.stringify(saved)) };
    }
  });
  vm.runInContext(functions + listeners + "\nglobalThis.fields = CREDENTIAL_FIELDS;", context);
  context.renderCredentialStates({ services: saved });
  const values = () => Object.fromEntries(services.map((service) => [service, $(context.fields[service].input).value]));
  const draft = () => services.forEach((service) => { $(context.fields[service].input).value = `new-test-only-${service}`; });
  const submit = () => $("credentialForm").events.submit[0]({ preventDefault() {} });
  return { context, $, saved, requests, confirmations, messages, closeButtons, values, draft, submit };
}

(async () => {
  const replacement = scenario();
  replacement.$("credentialMinimaxInput").value = "test-only-a;test-only-b";
  await replacement.submit();
  assert.equal(replacement.requests.length, 1);
  assert.equal(replacement.requests[0].url, "/api/settings/credentials");
  const operations = JSON.parse(JSON.stringify(replacement.requests[0].body.services));
  assert.deepEqual(operations.minimax, { action: "replace", accounts: ["test-only-a", "test-only-b"] });
  services.filter((service) => service !== "minimax").forEach((service) => assert.deepEqual(operations[service], { action: "keep" }));
  assert.equal(replacement.confirmations.length, 0, "Saving new values never asks to delete a key");
  assert.equal(replacement.$("credentialMinimaxInput").value, "");

  const empty = scenario();
  await empty.submit();
  assert.equal(empty.requests.length, 0, "Blank save cannot clear or rewrite any existing service");
  assert.match(empty.$("credentialResult").textContent, /保持不变/);

  const invalid = scenario();
  invalid.$("credentialMinimaxInput").value = Array.from({ length: 9 }, (_, i) => `test-only-${i}`).join(";");
  await invalid.submit();
  assert.equal(invalid.requests.length, 0);
  assert.ok(invalid.$("credentialMinimaxInput").value);

  const saveFailed = scenario();
  saveFailed.draft();
  const saveDraft = saveFailed.values();
  saveFailed.context.api = async () => { throw Error("Local storage unavailable"); };
  await saveFailed.submit();
  assert.deepEqual(saveFailed.values(), saveDraft, "A failed save leaves a retryable form");
  assert.equal(saveFailed.$("credentialSave").disabled, false);

  for (const service of services) {
    const deleting = scenario();
    deleting.draft();
    const before = deleting.values();
    await deleting.context.deleteCredential(service);
    assert.equal(deleting.requests.length, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(deleting.requests[0].body)), { services: { [service]: { action: "clear" } } }, "Deleting sends only the chosen service, without any form values");
    assert.deepEqual(deleting.values(), before, "Every unsaved input survives deleting any service");
    assert.equal(deleting.$(deleting.context.fields[service].remove).hidden, true);
    services.filter((other) => other !== service).forEach((other) => {
      assert.equal(deleting.saved[other].configured, true);
      assert.equal(deleting.$(deleting.context.fields[other].remove).hidden, false);
    });
    assert.equal(deleting.confirmations[0].focusCancel, true);
    assert.ok(deleting.confirmations[0].title.includes(deleting.context.fields[service].label));
  }

  const cancelled = scenario();
  cancelled.draft();
  const cancelledDraft = cancelled.values();
  cancelled.context.confirmDialog = async () => false;
  await cancelled.context.deleteCredential("mineru");
  assert.equal(cancelled.requests.length, 0);
  assert.deepEqual(cancelled.values(), cancelledDraft);
  assert.equal(cancelled.$("credentialSave").disabled, false);

  const deleteFailed = scenario();
  deleteFailed.draft();
  const failedDraft = deleteFailed.values();
  deleteFailed.context.api = async () => { throw Error("Local storage unavailable"); };
  await deleteFailed.context.deleteCredential("modelscope");
  assert.deepEqual(deleteFailed.values(), failedDraft);
  assert.equal(deleteFailed.$("credentialModelscopeDelete").hidden, false);
  assert.equal(deleteFailed.$("credentialModelscopeDelete").disabled, false);
  assert.match(deleteFailed.$("credentialResult").textContent, /未能删除\s*魔搭\s*密钥/);

  const pending = scenario();
  pending.draft();
  let accept;
  pending.context.confirmDialog = () => new Promise((resolve) => { accept = resolve; });
  const first = pending.context.deleteCredential("mineru");
  await pending.context.deleteCredential("minimax");
  await pending.submit();
  assert.equal(pending.requests.length, 0, "A pending deletion blocks concurrent delete/save operations");
  assert.ok(pending.closeButtons.every((node) => node.disabled));
  let prevented = false;
  pending.$("credentialDialog").events.cancel[0]({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true, "Esc cannot silently close a form during deletion");
  accept(false); await first;
  assert.ok(pending.closeButtons.every((node) => !node.disabled));

  const refreshFailed = scenario();
  refreshFailed.context.loadStatus = async () => { throw Error("Refresh unavailable"); };
  await refreshFailed.context.deleteCredential("siliconflow");
  assert.equal(refreshFailed.saved.siliconflow.configured, false);
  assert.match(refreshFailed.$("credentialResult").textContent, /^已删除\s*硅基流动\s*密钥.*状态暂未刷新/);
  assert.equal(refreshFailed.messages.at(-1).kind, "success", "A status refresh failure is not a deletion failure");

  const stale = scenario();
  let staleReply;
  const originalApi = stale.context.api;
  stale.context.api = (url, options) => options ? originalApi(url, options) : new Promise((resolve) => { staleReply = resolve; });
  const loading = stale.context.loadCredentialStates();
  await stale.context.deleteCredential("mineru");
  staleReply({ services: Object.fromEntries(services.map((service) => [service, { configured: true, count: 1 }])) });
  await loading;
  assert.equal(stale.$("credentialMineruDelete").hidden, true, "An older status response cannot revive a deleted service");

  console.log("Credential UI: separate save/delete, four-service isolation, retained drafts, confirmation/cancel, failure/retry, busy guard and stale status checks: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
