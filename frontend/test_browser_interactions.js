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
assert.ok(calls.some(value => value.url === "/api/window/available"), "the page asks whether a real desktop window is behind it");
// 1.25.51：窗口没关掉时，1.6 秒后恢复页面并补一次关闭请求 —— 不能永远白屏。
const watchdog = timers.find(timer => timer.delay === 1600);
assert.ok(watchdog, "closing schedules a watchdog");
watchdog.callback();
assert.ok(!classes.has("window-exiting"), "the page is restored when the window does not close");
assert.equal(calls.filter(value => value.url === "/api/window/close").length, 2,
  "the close request is retried once");
console.log("Self-drawn window controls: minimize and close reach the launcher, closing fades out and recovers: OK");

// 1.26.2：这一组按钮只在"眼前这个窗口没有标题栏"的时候出现：
//   · 题有据自己的窗口 → 有系统标题栏（自带三个按钮）→ 页面不画，两套并排会让人
//     不知道该点哪一个（1.25.90 删掉页面按钮就是这个理由）；
//   · 题有据自己的窗口被整屏显示或拖到屏幕边缘外 → 标题栏够不着 → 页面这份顶上；
//   · 在别的浏览器标签页里打开题有据 → 那个标签页自己没有标题栏 → 画出来，
//     这是从那儿关掉软件的唯一出口。
function windowStub({ search = "", available = true, caption = true } = {}) {
  const stub = {
    document: {
      addEventListener: () => {},
      readyState: "complete",
      hidden: false,
      getElementById: id => (["windowClose","windowMinimize","windowMaximize"].includes(id) ? button(id)
        : id === "windowControls" ? stub.group : null),
      body: { classList: { add(){}, remove(){} } },
    },
    location: { search },
    sessionStorage: { store: {}, setItem(k, v) { this.store[k] = v; }, getItem(k) { return this.store[k]; } },
    fetch: () => Promise.resolve({ json: () => Promise.resolve({ available, caption }) }),
    setTimeout: () => {},
  };
  stub.group = { hidden: true };
  return stub;
}

(async () => {
  // 别的标签页：没有标题栏 → 画出来。
  const tab = windowStub({ available: true, caption: true });
  vm.runInNewContext(source, { window: tab });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(tab.group.hidden, false, "在别的标签页里，这组按钮是唯一的出口 → 画出来");
  // 题有据自己的窗口，标题栏还在 → 不画第二套。
  const captioned = windowStub({ search: "?qbhost=app", available: true, caption: true });
  vm.runInNewContext(source, { window: captioned });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(captioned.group.hidden, true,
    "题有据自己的窗口里，标题栏的按钮管这件事，页面不画第二套");
  // 同一个窗口被整屏显示 → 标题栏够不着 → 页面这份顶上。
  const fullscreen = windowStub({ search: "?qbhost=app", available: true, caption: false });
  vm.runInNewContext(source, { window: fullscreen });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(fullscreen.group.hidden, false, "整屏显示时标题栏够不着，页面这组按钮顶上");
  // 背后根本没有桌面窗口 → 不显示。
  const bare = windowStub({ available: false });
  vm.runInNewContext(source, { window: bare });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(bare.group.hidden, true, "背后没有桌面窗口时（普通网页服务器），不显示");
  console.log("Window buttons appear only where there is no title bar of their own: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
