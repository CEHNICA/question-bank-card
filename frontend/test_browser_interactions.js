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

// 1.26.3：页面不再自绘窗口按钮。
// 宿主窗口是普通的浏览器窗口，标题栏自带最小化 / 最大化 / 关闭；页面再画一套就是
// 两套并排，老师会不知道该点哪一个。这条断言就是为了防止下回又有人加回来。
const source = fs.readFileSync(path.join(__dirname,"browser-interactions.js"),"utf8");
assert.ok(!source.includes("/api/window/"), "the page no longer asks the launcher to move the window");
for (const file of ["index.html","library.html"]) {
  const page = fs.readFileSync(path.join(__dirname,file),"utf8");
  assert.ok(!page.includes("windowControls"), `${file} has no self-drawn window buttons`);
  assert.ok(!page.includes('id="windowClose"'), `${file} has no self-drawn close button`);
  assert.ok(!page.includes('id="windowMinimize"'), `${file} has no self-drawn minimize button`);
  assert.ok(!page.includes('id="windowMaximize"'), `${file} has no self-drawn maximize button`);
}
const styles = fs.readFileSync(path.join(__dirname,"styles.css"),"utf8");
assert.ok(!styles.includes(".window-button"), "the styles for the self-drawn buttons are gone");
console.log("No self-drawn window buttons: the browser title bar is the only one: OK");
