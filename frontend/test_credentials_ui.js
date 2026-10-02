"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

assert.match(html, /id="settingsCredentialOpen"[^>]*>填写或更换密钥<\/button>/);
assert.match(html, /id="credentialDialog"[^>]*aria-labelledby="credentialTitle"/);
assert.doesNotMatch(html, /从开始菜单[^<]*配置 API/);

for (const provider of ["Mineru", "Modelscope", "Minimax", "Siliconflow"]) {
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
assert.match(html, /已经保存的密钥不会再显示出来/);
assert.match(html, /不写入题库、日志或项目文件/);
assert.match(html, /不上传文件、不消耗识读额度/);
assert.match(html, />加密保存<\/button>/);
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

console.log("credential settings UI static checks: OK");
