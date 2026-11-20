"use strict";

// 单宿主就地渲染与无 DOM 搬运回归测试：
// - 不再存在跨父节点挪动 #credentialUi 的易错逻辑；
// - 设置页始终挂载于 #settingsCredentialHost，hash (#ai/#api) 自动定位至解题配置段；
// - prepareSettingsLeave() 与 beforeunload 统一拦截未保存改动。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const apiJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");

assert.doesNotMatch(appJs, /credentialWasInline|mountCredentialUI\(/, "obsolete DOM reparenting state is removed from app.js");
assert.doesNotMatch(html, /id="credentialUi"/, "no detached #credentialUi container to move around");
assert.match(html, /id="settingsCredentialHost"/, "inline settings container stays on settings page");
assert.match(html, /id="credentialDialogBody"/, "modal dialog has its own dedicated mount container");
assert.match(apiJs, /panel\.id = "credentialAnswerPanel"/, "answer panel keeps anchor ID for #ai/#api deep links");
assert.match(appJs, /async function prepareSettingsLeave\(\)/, "settings leave guard stays wired in app.js");
assert.match(appJs, /window\.APISettings\?\.prepareLeave/, "settings leave guard delegates to APISettings");
assert.match(appJs, /window\.APISettings\?\.hasUnsavedChanges\?\.\(\)\s*\|\|\s*window\.APISettings\?\.isMutating\?\.\(\)/, "beforeunload protects unified API drafts");

console.log("inline mount & deep link checks: OK");
