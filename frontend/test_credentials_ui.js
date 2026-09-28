"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

assert.match(html, /id="settingsCredentialOpen"[^>]*>配置 API 与账号池<\/button>/);
assert.match(html, /id="credentialDialog"[^>]*aria-labelledby="credentialTitle"/);
assert.doesNotMatch(html, /从开始菜单[^<]*配置 API/);

for (const provider of ["Mineru", "Minimax", "Siliconflow"]) {
  assert.match(html, new RegExp(`id="credential${provider}Input"[^>]*type="password"`));
  assert.match(html, new RegExp(`id="credential${provider}Clear"[^>]*type="checkbox"`));
  assert.match(html, new RegExp(`id="credential${provider}State"`));
}

assert.match(js, /api\("\/api\/settings\/credentials"\)/);
assert.match(js, /api\("\/api\/settings\/credentials",\s*\{\s*method:\s*"POST"/);
assert.match(js, /action:\s*"replace",\s*accounts/);
assert.match(js, /action:\s*"keep"/);
assert.match(js, /action:\s*"clear"/);
assert.match(js, /resetCredentialInputs\(\);[\s\S]*?renderCredentialStates\(result\)/);
assert.match(js, /catch \(error\) \{[\s\S]*?resetCredentialInputs\(\);/);
assert.match(js, /async function loadStatus\(\)[\s\S]*?return false;[\s\S]*?return true;/);
assert.match(js, /toast\(message, "success"\);[\s\S]*?const refreshed = await loadStatus\(\);[\s\S]*?if \(!refreshed\)/);
assert.match(js, /function openSettings\(\)[\s\S]*?\$\("settingsDialog"\)\.showModal\(\);[\s\S]*?void loadStatus\(\);/);
assert.match(html, /不会读取或显示已经保存的密钥/);
assert.match(html, /不写入题库、日志或项目文件/);
assert.match(html, /不上传文件、不消耗额度的官网验证/);
assert.match(html, />验证并加密保存<\/button>/);

console.log("credential settings UI static checks: OK");
