"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const apiCss = fs.readFileSync(path.join(__dirname, "api-redesign.css"), "utf8");
const stylesCss = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const apiSettingsJs = fs.readFileSync(path.join(__dirname, "api-settings.js"), "utf8");
const assistantConnectJs = fs.readFileSync(path.join(__dirname, "assistant-connect.js"), "utf8");
const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

describe("录题界面设置弹窗统一与交互细节契约", () => {
  it("index.html 包含 Eagle 同款设置弹窗外壳、通俗只切图文案与现代试卷操作分组", () => {
    assert.match(html, /class="settings-shell"/);
    assert.match(html, /id="settingsOverlayClose"/);
    assert.match(html, /id="emptyConnectAi"[^>]*>接豆包 \/ WorkBuddy 帮录<\/button>/);
    assert.match(html, /id="uploadCutOnly"[\s\S]*?只切图，不转文字/);
    assert.doesNotMatch(html, /MinerU 只切题/);
    assert.match(html, /class="paper-menu-group-label"[\s\S]*?基础与切题/);
    assert.match(html, /class="paper-notes-badge"/);
  });

  it("api-redesign.css 全屏铺满浮层消除左右灰边，并保留 192px 双栏与窄屏回退", () => {
    assert.match(apiCss, /\.api-setup-entry\s*\{\s*display:\s*none;\s*\}/);
    assert.match(apiCss, /#settingsDialog\s+\.settings-nav\s*\{[\s\S]*?width:\s*192px\s*!important;/);
    assert.match(apiCss, /#settingsHome\.is-modal-overlay\s*\{[\s\S]*?max-width:\s*none\s*!important;[\s\S]*?margin:\s*0\s*!important;/);
    assert.match(apiCss, /\[data-settings-tab="settingsGeneral"\]::before/);
    assert.match(apiCss, /@media\s*\(max-width:\s*760px\)\s*\{[\s\S]*?#settingsDialog\.settings-panel/);
    assert.match(stylesCss, /\.paper-menu-group-label\s*\{/);
  });

  it("app.js 仅通过右上角关闭按钮或 Esc 关闭设置浮层（不点遮罩误关），且受离开守卫保护", () => {
    assert.match(appJs, /function\s+openSettingsOverlay\s*\(/);
    assert.match(appJs, /async\s+function\s+closeSettingsOverlay\s*\([\s\S]*?await\s+prepareSettingsLeave\(\)/);
    assert.doesNotMatch(appJs, /event\.target\s*===\s*\$\("settingsHome"\)/);
    assert.match(appJs, /"ArrowUp",\s*"ArrowDown"/);
    assert.match(appJs, /\$\("emptyConnectAi"\)\?\.addEventListener\("click"/);
    assert.doesNotMatch(appJs, /\$\("settingsDialog"\)\.showModal\(\)/);
  });

  it("api-settings.js 与 assistant-connect.js 简洁表达交给 AI 助手与两种配合方式", () => {
    assert.match(apiSettingsJs, /createEl\("span",\s*"",\s*"交给 AI 助手"\)/);
    assert.doesNotMatch(apiSettingsJs, /暂不配置/);
    assert.match(assistantConnectJs, /两种配合方式[\s\S]*?MinerU/);
  });

  it("原卷编辑窗口（#pageDialog）支持调整范围/管理配图/框选识读一键互切并与 Eagle 重框窗对齐", () => {
    assert.match(html, /id="pageModeSwitch"[\s\S]*?id="pageModeRegions"[\s\S]*?id="pageModeFigures"[\s\S]*?id="pageModeRead"/);
    assert.match(appJs, /function\s+syncPageModeTabs\s*\(/);
    assert.match(appJs, /\$\("pageModeSwitch"\)\?\.addEventListener\("click"/);
    assert.match(stylesCss, /\.page-mode-switch\s*\{/);
    assert.match(stylesCss, /\.page-mode-tab\.is-active/);
    assert.match(stylesCss, /#pageDialog\s+\.figure-slot-menu\s*\{/);
    assert.match(stylesCss, /#pageDialog\s+\.grip\s*\{/);
    assert.match(eagleHtml, /id="originalPaperModal"[^>]*onclick="if \(!opState\.recropActive\) closeOriginalPaperModal\(\)"/);
  });

  it("录题界面合并重复编辑入口，只保留唯一「编辑」与「在题库中看」；Eagle 重框点叉号退回编辑器且带回 return_to", () => {
    const solEditorJs = fs.readFileSync(path.join(__dirname, "eagle-solution-editor.js"), "utf8");
    const unifiedEditorJs = fs.readFileSync(path.join(__dirname, "library-unified-editor.js"), "utf8");
    assert.doesNotMatch(appJs, /button\("编答案\/解析"/);
    assert.match(appJs, /button\("编辑"/);
    assert.match(appJs, /button\("在题库中看"/);
    assert.match(eagleHtml, /opState\.returnToEditor\s*=/);
    assert.match(eagleHtml, /openSolutionEditorForId\(backEditor\.qid/);
    assert.match(solEditorJs, /const ASSET_VER = "20261127a"/);
    assert.match(solEditorJs, /returnTo:\s*options\.returnTo \|\| ""/);
    assert.match(unifiedEditorJs, /typeof openOptions\.onRecrop === "function"/);
    assert.match(unifiedEditorJs, /close\(\{\s*stayInPlace:\s*true\s*\}\)/);
    assert.match(unifiedEditorJs, /root\.location\.href\s*=\s*returnTo/);
  });

  it("录题界面精修：分屏隐藏重复原图、宽屏工具栏显示清晰文字标签、重开分屏立即解码绘制", () => {
    const splitJs = fs.readFileSync(path.join(__dirname, "intake-split-canvas.js"), "utf8");
    assert.match(splitJs, /img\.loading = "eager";/);
    assert.match(splitJs, /img\.decoding = "sync";/);
    assert.match(stylesCss, /body\.entry-page \.paper-workspace\.split-open \.card:not\(\.compact\):not\(\.editing\) > \.card-source\s*\{\s*display:\s*none;\s*\}/);
    assert.match(stylesCss, /body\.entry-page \.tool-group-icons \.tool-label\s*\{[\s\S]*?position:\s*static;/);
    assert.match(stylesCss, /body\.entry-page \.intake-stepper\s*\{[\s\S]*?display:\s*flex;/);
  });
});
