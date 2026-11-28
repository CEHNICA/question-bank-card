"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("社区上传弹窗支持关闭二选一提示、后台浮动进度条与上传记录中心", () => {
  assert.match(html, /id="commUploadClosePrompt"/, "存在关闭时二选一提示弹窗");
  assert.match(html, /onclick="confirmCancelCommunityUploadFromPrompt\(\)"[^>]*>取消上传<\/button>/);
  assert.match(html, /onclick="continueCommunityUploadInBackground\(\)"[^>]*>后台继续<\/button>/);

  assert.match(html, /id="btnCommunityUploadCenter"/, "顶栏存在上传进度与记录入口");
  assert.match(html, /id="commUploadToolbarLabel"/, "顶栏存在后台进度百分比标签");
  assert.match(html, /id="eagleUploadFloatingBar"/, "存在后台浮动进度条");
  assert.match(html, /id="communityUploadCenterModal"/, "存在上传进度与上传记录弹窗");
  assert.match(html, /id="menuBadgeUploadCount"/, "主菜单存在上传记录入口徽标");

  assert.match(html, /\/api\/backup\/community-upload\/start/);
  assert.match(html, /\/api\/backup\/community-upload\/tasks/);
});

test("主菜单在缩小窗口或缩放界面时动态计算 maxHeight 避免底部被遮挡无法滚动到底", () => {
  assert.match(html, /function positionEagleAppMainMenu\(/);
  assert.match(html, /function getVisibleViewportHeight\(/);
  assert.doesNotMatch(html, /id="eagleAppMainMenu"[^>]*max-h-\[84vh\]/);
  assert.match(html, /overscroll-behavior:\s*contain/);
  assert.match(html, /has-update-pill/, "有新版本时更新徽标应换到独立底行，避免把左侧标题挤成竖排");
  assert.match(html, /id="eagleMenuFullscreenBtn"/, "主菜单应提供软件窗口全屏按钮");
  assert.match(html, /e\.key === 'F11'/, "工作台支持 F11 切换软件窗口全屏");
  assert.match(html, /function toggleAppFullscreen\(/);
});

test("选中未建页的题目时后台预渲染题面图，让 OneNote 一次带图建页", () => {
  assert.match(html, /function scheduleOneNotePrewarm\(/);
  assert.match(html, /\/onenote\/prewarm/);
  assert.match(html, /if \(!hasNote\) scheduleOneNotePrewarm\(q\.id\)/);
});

test("题库大文件夹性能关键路径已走缓存与单遍索引优化", () => {
  assert.match(html, /function readUserFoldersCached\(/);
  assert.match(html, /function readDocFolderOverridesCached\(/);
  assert.match(html, /function readDocPlacementsCached\(/);
  assert.match(html, /function buildCategorizedQuestionIdSet\(/);
  assert.match(html, /function buildQuestionTagIndexMap\(/);
  assert.match(html, /function syncFolderTreeActiveHighlight\(/);
  assert.match(
    html,
    /\/api\/library\?document=\$\{encodeURIComponent\(key\)\}&bulk=1&limit=\$\{pageSize\}/,
    "按卷加载必须走 bulk=1&limit=500 避免逐页重算全库 facets"
  );
});
