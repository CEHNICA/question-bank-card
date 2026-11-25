"use strict";
// 左侧空状态别再堆说明文案；文件夹/考题属性悬浮统一走自定义气泡。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

describe("左侧空状态与全局悬浮气泡", () => {
  it("1. 智能文件夹/文件夹空态不再塞说明假节点", () => {
    assert.doesNotMatch(html, /组合筛选后点击 \+ 存为智能视图/);
    assert.doesNotMatch(html, /点击 \+ 新建文件夹或拖放题目至此/);
    assert.doesNotMatch(html, /点击 \+ 新建知识点标签/);
    assert.doesNotMatch(html, /点击 \+ 新建方法标签/);
    assert.match(html, /\/\/ 空着就空着：靠分区右侧 \+ 新建/);
    assert.match(html, /children: customSmartNodes/);
    assert.match(html, /children: folderChildren/);
    assert.match(html, /children: knowTags/);
    assert.match(html, /children: methTags/);
  });

  it("2. 目录树与考题属性用 data-tip，不用系统 title", () => {
    assert.match(html, /function setEagleTip\(/);
    assert.match(html, /function installEagleHoverTipDelegation\(/);
    assert.match(html, /installEagleHoverTipDelegation\(\)/);
    assert.match(html, /data-tip-title="\$\{tipTitle\}"/);
    assert.match(html, /whenTruncated:\s*true/);
    assert.match(html, /data-tip-when-truncated="1"/);
    assert.match(html, /function isEagleTipHostTruncated\(/);
    assert.match(html, /data-tip-title="核验证据"/);
    // 左树节点不再拼原生 title=
    assert.doesNotMatch(
      html,
      /const timeTip = node\.createdLabel\s*\?\s*` title="/
    );
  });

  it("3. 全局气泡是半透明毛玻璃，并把原生 title 收进来", () => {
    assert.match(html, /--eagle-tip-bg:\s*rgba\(255,\s*255,\s*255,\s*0\.48\)/);
    assert.match(html, /backdrop-filter:\s*blur\(18px\)/);
    assert.match(html, /data-tip-native/);
    assert.match(html, /closest\('\[title\], \[data-tip-native\]'\)/);
    assert.match(html, /function shouldSkipRedundantNativeTip\(/);
  });

  it("4. 左栏截断文件夹名贴在侧栏右侧，不盖住下方文件夹", () => {
    assert.match(html, /is-tree-peek/);
    assert.match(html, /data-place="right"/);
    assert.match(html, /sideRect\.right \+ 8/);
    assert.match(html, /#folderTreeContainer, #leftFolderSidebar/);
  });
});
