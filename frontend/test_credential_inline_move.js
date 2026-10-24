"use strict";

// 1.26.9：API 配置改成"整份摆在一页上"之后，有三件事必须钉住，否则老师会
// 遇到"填的密钥不见了""按钮点了没反应""整页配置凭空消失"：
//   1. 整份配置只有一份（同一个 DOM 在设置页和弹窗之间搬，不复制）；
//   2. 离开设置页时，刚填还没保存的密钥要问一句（别静默丢掉）；
//   3. 弹窗里的「取消」按钮要点得动（它搬出弹窗以后不再被 "dialog [data-close]" 命中）。
// 之前这几条一条测试都没有 —— 1.26.9 的审计就是靠人眼看出来的。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const dom = require("./credential-test-dom.js");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const start = js.indexOf("  function settingsEngineChoices(");
const end = js.indexOf("  function renderSettingsTask(");
const functions = js.slice(start, end);
const listenersStart = js.indexOf('  Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {', js.indexOf('  $("questionTrash").addEventListener("click", openQuestionTrash);'));
const listeners = js.slice(listenersStart, js.indexOf("  // Queue the values captured", listenersStart));
// 离开这一页的守卫在切片之外，单独取一段出来测（它守着"刚填的密钥别悄悄没了"）。
const leaveStart = js.indexOf("  async function prepareSettingsLeave()");
const afterLeave = js.slice(leaveStart + 10);
const nextTop = afterLeave.search(/\n  (async )?function /);
const leaveGuard = js.slice(leaveStart, leaveStart + 10 + (nextTop > 0 ? nextTop : afterLeave.length));
assert.ok(start > 0 && end > start && listenersStart > 0 && leaveStart > 0 && leaveGuard.includes("prepareSettingsLeave"),
  "the credential slices are still where we expect them");

const turns = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };

function scenario() {
  const nodes = new Map(), requests = [], confirmations = [], focuses = [];
  let scenarioAnswer = true;
  const saved = {
    mineru: { configured: true, count: 1 }, modelscope: { configured: false, count: 0 },
    minimax: { configured: true, count: 2 }, siliconflow: { configured: false, count: 0 }
  };
  function $(id) {
    if (!nodes.has(id)) nodes.set(id, { ...dom.node(id),
      addEventListener(type, fn) { (this.events[type] ||= []).push(fn); },
      showModal() { this.open = true; },
      close() { if (!this.open) return; this.open = false; this.events.close?.forEach((fn) => fn({ target: this })); },
      focus(options) { focuses.push({ id, options }); }
    });
    return nodes.get(id);
  }
  const context = vm.createContext({ $, console, state: { status: { engines: { saved: {}, choices: [] } } },
    credentialSourceNote: () => "", el: dom.el, icon: dom.icon, AbortController, setTimeout, clearTimeout,
    modelFormDirty: false, lastModelSave: null, credentialModelGuard: null,
    window: { addEventListener() {}, location: { hash: "", pathname: "/settings" } }, document: { addEventListener() {}, activeElement: null },
    requestAnimationFrame() { return 0; }, cancelAnimationFrame() {},
    anyDialogOpen() { return false; },
    toast() {}, confirmDialog: async (options) => { confirmations.push(options); return scenarioAnswer; },
    loadStatus: async () => true,
    api: async (url, options) => {
      requests.push({ url, ...(options || {}) });
      return { services: JSON.parse(JSON.stringify(saved)) };
    }
  });
  vm.runInContext(functions + listeners + leaveGuard + "\nglobalThis.fields = CREDENTIAL_FIELDS;", context);
  return { context, $, requests, confirmations, focuses, saved,
    set answer(value) { scenarioAnswer = value; }, get answer() { return scenarioAnswer; } };
}

(async () => {
  // ① 只有一份：设置页摆出来 → 弹窗借走 → 关掉还回来。
  const moved = scenario();
  await moved.context.showCredentialInline();
  const ui = moved.$("credentialUi");
  assert.equal(ui.parentElement.id, "settingsCredentialHost", "the configuration block sits on the settings page");
  assert.equal(ui.hidden, false, "and it is visible there");
  await moved.context.openCredentialSettings();
  assert.equal(ui.parentElement.id, "credentialDialogBody", "opening the window moves the same block into it");
  assert.equal(moved.$("credentialDialogBody").children.filter((child) => child.id === "credentialUi").length, 1,
    "the dialog holds exactly one copy");
  assert.equal(moved.$("settingsCredentialHost").children.filter((child) => child.id === "credentialUi").length, 0,
    "and the settings page gave it up");
  moved.$("credentialDialog").close();
  await turns();
  assert.equal(ui.parentElement.id, "settingsCredentialHost", "closing the window gives the block back to the settings page");
  assert.equal(ui.hidden, false, "and it is visible again — the page must not lose its configuration");

  // ② 刚填了还没保存的密钥，离开这一页要问一句。
  const leaving = scenario();
  await leaving.context.showCredentialInline();
  leaving.$("credentialMineruInput").value = "offline-unsaved-token";
  leaving.answer = false;
  const refused = await leaving.context.prepareSettingsLeave();
  assert.equal(refused, false, "choosing 继续设置 must keep the teacher on the page");
  assert.equal(leaving.confirmations.length, 1, "and the teacher is asked");
  assert.match(leaving.confirmations[0].title, /API 配置还没保存/);
  assert.equal(leaving.$("credentialMineruInput").value, "offline-unsaved-token", "the draft is still there");
  leaving.answer = true;
  const allowed = await leaving.context.prepareSettingsLeave();
  assert.equal(allowed, true, "choosing 放弃并离开 lets the teacher go");
  assert.equal(leaving.$("credentialMineruInput").value, "", "and the draft is cleared");

  // ③ 弹窗里的「取消」按钮要点得动（它搬出弹窗后不再被 "dialog [data-close]" 命中）。
  const cancelled = scenario();
  await cancelled.context.openCredentialSettings();
  const cancel = cancelled.$("credentialCancel");
  assert.ok(cancel.events.click?.length, "the cancel button has a listener of its own");
  cancel.events.click[0]();
  await turns();
  assert.equal(cancelled.$("credentialDialog").open, false, "clicking 取消 closes the window");

  // ④ 服务列表还没读到之前，勾选行不让点（免得勾完又被回填弹回去）。
  const early = scenario();
  early.context.state.status = { engines: null };
  early.context.syncCredentialVisionPanel();
  for (const provider of ["modelscope", "minimax", "siliconflow", "assistant"]) {
    assert.equal(early.$(`credentialVision-${provider}`).disabled, true, "the tick is disabled before the service list arrives");
  }
  early.context.state.status = { engines: { saved: {}, choices: [] } };
  early.context.syncCredentialVisionPanel();
  for (const provider of ["modelscope", "minimax", "siliconflow", "assistant"]) {
    assert.equal(early.$(`credentialVision-${provider}`).disabled, false, "and enabled once it has");
  }
  console.log("API configuration moves as one block, guards unsaved keys, and keeps its cancel button: OK");
})().catch((error) => { console.error(error); process.exit(1); });
