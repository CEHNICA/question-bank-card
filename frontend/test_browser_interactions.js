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
const group = { hidden: true };
const hostRoot = {
  document: {
    addEventListener: () => {},
    readyState: "complete",
    hidden: false,
    getElementById: id => (["windowClose","windowMinimize","windowMaximize"].includes(id) ? button(id)
      : id === "windowControls" ? group : null),
    body: { classList: { add: name => classes.add(name), remove: name => classes.delete(name) } },
  },
  fetch: (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/available")) return Promise.resolve({ json: () => Promise.resolve({ available: true, caption: false }) });
    return Promise.resolve();
  },
  // 关窗先淡出再交给宿主：160ms 的淡出定时器同步执行；1.6 秒的兜底定时器留到后面手动触发。
  setTimeout: (callback, delay) => { timers.push({ callback, delay }); if (!delay || delay < 1000) callback(); },
};
vm.runInNewContext(source, { window: hostRoot });
listeners["windowClose:click"]();
listeners["windowMinimize:click"]();
listeners["windowMaximize:click"]();
assert.deepEqual(calls.map(value => value.url),
  ["/api/window/available", "/api/window/close", "/api/window/minimize", "/api/window/maximize"],
  "the self-drawn window buttons ask the launcher instead of relying on window.close()");
assert.deepEqual(calls.filter(value => value.options?.method === "POST").map(value => value.options.method), ["POST", "POST", "POST"]);
assert.deepEqual(calls.filter(value => value.options?.method === "POST").map(value => value.options.headers["X-QB-Request"]), ["1", "1", "1"],
  "the custom header is exactly what the service checks before acting");
assert.ok(classes.has("window-exiting"), "closing fades the page out first");
// 1.26.0：只有真在桌面程序里跑着才显示这一组按钮 —— 普通浏览器标签页里显示也是摆设。
assert.ok(calls.some(value => value.url === "/api/window/available"), "the page asks whether a real desktop window is behind it");
// 1.25.51：窗口没关掉时，1.6 秒后恢复页面并补一次关闭请求 —— 不能永远白屏。
const watchdog = timers.find(timer => timer.delay === 1600);
assert.ok(watchdog, "closing schedules a watchdog");
watchdog.callback();
assert.ok(!classes.has("window-exiting"), "the page is restored when the window does not close");
assert.equal(calls.filter(value => value.url === "/api/window/close").length, 2,
  "the close request is retried once");
console.log("Self-drawn window controls: minimize and close reach the launcher, closing fades out and recovers: OK");

// 1.26.0：真在桌面程序里跑着、而且标题栏够不着时，这一组按钮才露出来；
// 普通浏览器标签页里不显示，标题栏还在时也不显示（避免两套控件并排）。
(async () => {
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(group.hidden, false, "the page shows its window buttons when the title bar is out of reach");
  const bare = { document: { addEventListener: () => {}, readyState: "complete", hidden: false,
    getElementById: id => (["windowClose","windowMinimize","windowMaximize"].includes(id) ? button(id)
      : id === "windowControls" ? { hidden: true } : null), body: { classList: { add(){}, remove(){} } } },
    fetch: url => Promise.resolve({ json: () => Promise.resolve({ available: false, caption: false }) }),
    setTimeout: () => {} };
  vm.runInNewContext(source, { window: bare });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(bare.document.getElementById("windowControls").hidden, true,
    "a plain browser tab gets no window buttons — they would do nothing there");
  // 标题栏还在的时候也不画 —— 两套控件并排，老师会不知道该点哪一个。
  const captioned = { document: { addEventListener: () => {}, readyState: "complete", hidden: false,
    getElementById: id => (["windowClose","windowMinimize","windowMaximize"].includes(id) ? button(id)
      : id === "windowControls" ? { hidden: true } : null), body: { classList: { add(){}, remove(){} } } },
    fetch: url => Promise.resolve({ json: () => Promise.resolve({ available: true, caption: true }) }),
    setTimeout: () => {} };
  vm.runInNewContext(source, { window: captioned });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(captioned.document.getElementById("windowControls").hidden, true,
    "while the title bar is reachable, the page does not draw a second set of buttons");
  console.log("Window buttons appear only when the title bar is out of reach: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
