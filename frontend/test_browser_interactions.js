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
