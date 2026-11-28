"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { create } = require("./qb-update.js");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const newer = { update_available: true, current: "2.3.19", latest: "9.9.9" };
const ready = { status: "ready", active: false, can_install: true, version: "9.9.9" };
function harness(request, confirm = async () => true) {
  const states = [];
  const calls = [];
  const controller = create({ request: async (path, options) => { calls.push({ path, options }); return request(path, options); },
    onChange: (state) => states.push(state), confirmInstall: confirm });
  return { controller, calls, state: () => states.at(-1) };
}

test("下载与安装分开：最新版本、无确认、取消确认均不发起安装", async () => {
  const h = harness(async () => ready, async () => false);
  h.controller.updateLatest({ update_available: false, latest: "2.3.19", current: "2.3.19" });
  await h.controller.start();
  assert.equal(h.calls.length, 0);
  h.controller.updateLatest(newer);
  await h.controller.start();
  assert.equal(h.calls[0].path, "/api/update/download/start");
  assert.equal(h.state().label, "安装更新");
  assert.equal(h.state().disabled, false);
  await h.controller.start();
  assert.equal(h.calls.length, 1);
  assert.equal(h.state().label, "安装更新");
});

test("确认安装只发一次确认请求，不重新下载", async () => {
  let release;
  const h = harness(async () => ({ status: "succeeded", active: false }), () => new Promise((resolve) => { release = resolve; }));
  h.controller.sync(ready);
  const started = h.controller.start();
  await h.controller.start();
  assert.equal(h.state().disabled, true);
  release(true);
  await started;
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, "/api/update/install/confirm");
  assert.deepEqual(h.calls[0].options.body, { confirmed: true });
  assert.equal(h.state().disabled, true);
});

test("下载时打开弹窗或检查结果返回均不重新启用按钮", () => {
  const h = harness(async () => ({}));
  h.controller.sync({ status: "downloading", active: true, installing: false });
  h.controller.updateLatest(newer);
  assert.equal(h.state().label, "正在下载…");
  assert.equal(h.state().disabled, true);
  assert.equal(h.state().checkDisabled, true);
  assert.equal(h.state().cancelVisible, true);
  h.controller.sync({ status: "cancelling", active: true });
  assert.equal(h.state().cancelDisabled, true);
  h.controller.sync({ status: "cancelled", active: false });
  assert.equal(h.state().label, "下载更新");
  assert.equal(h.state().cancelVisible, false);
});

test("检查失败显示失败，不把旧的更新信息当最新结果", async () => {
  const h = harness(async () => { throw new Error("官网超时"); });
  h.controller.updateLatest(newer);
  await h.controller.check();
  assert.equal(h.state().disabled, true);
  assert.equal(h.state().error, true);
  assert.equal(h.state().message, "官网超时");
});

test("准备请求未完成时阻止重复下载；取消后可重试", async () => {
  let release;
  const h = harness(async (path) => path.endsWith("start") ? new Promise((resolve) => { release = resolve; }) : { status: "cancelled", active: false });
  h.controller.updateLatest(newer);
  const start = h.controller.start();
  await h.controller.start();
  assert.equal(h.calls.length, 1);
  assert.equal(h.state().disabled, true);
  release({ status: "cancelled", active: false });
  await start;
  assert.equal(h.state().disabled, false);
});

test("取消必须调用后台取消接口；安装开始后不能取消", async () => {
  const h = harness(async () => ({ status: "cancelled", active: false }));
  h.controller.sync({ status: "downloading", active: true });
  await h.controller.cancel();
  assert.equal(h.calls[0].path, "/api/update/download/cancel");
  h.controller.sync({ status: "backing_up", active: true, installing: true });
  await h.controller.cancel();
  assert.equal(h.calls.length, 1);
  assert.equal(h.state().cancelVisible, false);
});

test("下载失败快照恢复重试；安装失败保留已校验安装包", async () => {
  const h = harness(async () => ({ status: "failed", active: false, error: "下载校验失败" }));
  h.controller.updateLatest(newer);
  h.controller.sync({ status: "downloading", active: true });
  await h.controller.refresh();
  assert.equal(h.state().disabled, false);
  assert.equal(h.state().label, "下载更新");
  assert.equal(h.state().message, "下载校验失败");
  h.controller.sync({ status: "failed", active: false, can_install: true, error: "安装未启动" });
  assert.equal(h.state().disabled, false);
  assert.equal(h.state().label, "安装更新");
});

test("Eagle 请求层把失败进度交给控制器，而不是当成进度读取失败", async () => {
  const html = fs.readFileSync(path.join(__dirname, "eagle/index.html"), "utf8");
  const source = html.match(/    function getEagleUpdateController\([\s\S]*?\n    \}/)[0];
  let config;
  const failed = { status: "failed", active: false, error: "校验失败" };
  const c = vm.createContext({ API_BASE: "", QBUpdate: { create(options) { config = options; return {}; } },
    document: { getElementById() { return null; } }, fetch: async () => ({ ok: true, json: async () => failed }) });
  vm.runInContext("let eagleUpdateController=null; let latestOfficialUpdateInfo=null;\n" + source + "\ngetEagleUpdateController();", c);
  assert.equal((await config.request("/api/update/progress")).status, "failed");
  c.fetch = async () => ({ ok: false, json: async () => ({ error: "官网连接失败" }) });
  await assert.rejects(config.request("/api/update/check"), /官网连接失败/);
});
