const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const ROOT = path.resolve(__dirname, "..");

test("AssistantConnect component exposes Doubao, WorkBuddy, MiniMax, MCP guides and copy buttons", () => {
  const js = fs.readFileSync(path.join(ROOT, "frontend", "assistant-connect.js"), "utf8");
  for (const token of [
    "豆包电脑版",
    "WorkBuddy / MiniMax",
    "Cursor / Trae / Cherry / Codex",
    "/api/assistant/connect",
    "/api/assistant/connect/setup",
    "/api/assistant/connect/open-skill-zip",
    "/api/assistant/connect/skill-zip",
    "一键复制 MCP 配置 (JSON)",
    "复制 AI 开场指令",
    "window",
  ]) {
    assert.ok(js.includes(token), `Missing token in assistant-connect.js: ${token}`);
  }

  const eagleHtml = fs.readFileSync(path.join(ROOT, "frontend", "eagle", "index.html"), "utf8");
  assert.match(eagleHtml, /id="eaglePrefAiConnectMount"/);
  assert.match(eagleHtml, /\/static\/assistant-connect\.js/);
  assert.match(eagleHtml, /openEaglePreferencesModal\('help'\)/);

  const mainHtml = fs.readFileSync(path.join(ROOT, "frontend", "index.html"), "utf8");
  assert.match(mainHtml, /id="settingsAiConnectMount"/);
  assert.match(mainHtml, /\/static\/assistant-connect\.js/);
});

test("Website showcases 3-step AI connect tabs and one-click copy controls", () => {
  const webHtml = fs.readFileSync(path.join(ROOT, "website", "public", "index.html"), "utf8");
  const webJs = fs.readFileSync(path.join(ROOT, "website", "public", "app.js"), "utf8");
  for (const id of [
    "agentConnect",
    "webConnectSteps",
    "webConnectCopyCode",
    "webConnectCodeBody",
    "webConnectDownloadSkillBtn",
    "webConnectCopyIntro",
  ]) {
    assert.match(webHtml, new RegExp(`id="${id}"`), `Missing #${id} in website/public/index.html`);
  }
  for (const tab of ["doubao", "workbuddy", "minimax", "mcp"]) {
    assert.match(webHtml, new RegExp(`data-connect-tab="${tab}"`), `Missing website tab ${tab}`);
  }
  assert.match(webJs, /function renderConnectTab\(tabId\)/);
  assert.match(webJs, /flashCopiedButton/);
});
