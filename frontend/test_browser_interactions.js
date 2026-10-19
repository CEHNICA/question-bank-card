"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const events = [];
const root = { document: { addEventListener: (name, callback) => events.push({ name, callback }) } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"browser-interactions.js"),"utf8"), { window:root });
assert.deepEqual(events.map(value=>value.name), ["contextmenu"], "Normal left click and keyboard copy/paste are not intercepted");
for (const editable of [false,true]) {
  let blocked = false;
  events[0].callback({ target:{ closest:()=>editable?{}:null }, preventDefault:()=>{blocked=true;} });
  assert.equal(blocked,!editable,"Plain areas suppress browser menus; text fields retain native copying/pasting");
}
console.log("Application context menus: reading area hidden, editable menu and normal clicks retained: OK");

// 页面自绘的窗口控件。宿主窗口去掉系统标题栏之后，这两个按钮是唯一的最小化/关闭
// 入口：window.close() 关不掉命令行 --app 打开的窗口，必须真的把请求发给启动器。
// 只留上面那个只带 addEventListener 的 document 桩是测不到的，这里换一个完整桩。
const source = fs.readFileSync(path.join(__dirname,"browser-interactions.js"),"utf8");
const listeners = {}, calls = [], classes = new Set(), timers = [];
const button = id => ({ addEventListener: (name, cb) => { listeners[`${id}:${name}`] = cb; } });
const hostRoot = {
  document: {
    addEventListener: () => {},
    readyState: "complete",
    hidden: false,
    getElementById: id => (["windowClose","windowMinimize"].includes(id) ? button(id) : null),
    body: { classList: { add: name => classes.add(name), remove: name => classes.delete(name) } },
  },
  fetch: (url, options) => { calls.push({ url, options }); return Promise.resolve(); },
  // 关窗先淡出再交给宿主：160ms 的淡出定时器同步执行；1.6 秒的兜底定时器留到后面手动触发。
  setTimeout: (callback, delay) => { timers.push({ callback, delay }); if (!delay || delay < 1000) callback(); },
};
vm.runInNewContext(source, { window: hostRoot });
listeners["windowClose:click"]();
listeners["windowMinimize:click"]();
assert.deepEqual(calls.map(value => value.url), ["/api/window/close", "/api/window/minimize"],
  "the self-drawn window buttons ask the launcher instead of relying on window.close()");
assert.deepEqual(calls.map(value => value.options.method), ["POST", "POST"]);
assert.deepEqual(calls.map(value => value.options.headers["X-QB-Request"]), ["1", "1"],
  "the custom header is exactly what the service checks before acting");
assert.ok(classes.has("window-exiting"), "closing fades the page out first");
// 1.25.51：窗口没关掉时，1.6 秒后恢复页面并补一次关闭请求 —— 不能永远白屏。
const watchdog = timers.find(timer => timer.delay === 1600);
assert.ok(watchdog, "closing schedules a watchdog");
watchdog.callback();
assert.ok(!classes.has("window-exiting"), "the page is restored when the window does not close");
assert.equal(calls.filter(value => value.url === "/api/window/close").length, 2,
  "the close request is retried once");
console.log("Self-drawn window controls: minimize and close reach the launcher, closing fades out and recovers: OK");
