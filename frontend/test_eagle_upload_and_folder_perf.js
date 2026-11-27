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
