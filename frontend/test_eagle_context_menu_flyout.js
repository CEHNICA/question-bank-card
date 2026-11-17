"use strict";
// 右键菜单：标签区不说「子文件夹」；长列表悬停飞出，不整段撑开。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function bodyOf(source, signature) {
  const start = source.indexOf(signature);
  if (start < 0) return "";
  let i = start + signature.length;
  let depth = 0;
  let begun = false;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "{") {
      depth += 1;
      begun = true;
    } else if (ch === "}") {
      depth -= 1;
      if (begun && depth === 0) return source.slice(start, i + 1);
    }
  }
  return "";
}

test("Eagle 右键菜单：标签文案与飞出分组", async (t) => {
  await t.test("1. 标签分区说子标签，不说子文件夹", () => {
    const labels = bodyOf(html, "function treeExpandCollapseLabels");
    assert.ok(labels, "应有 treeExpandCollapseLabels");
    assert.match(labels, /展开全部子标签/);
    assert.match(labels, /收起全部子标签/);
    assert.match(labels, /展开全部子文件夹/);
    const ctx = bodyOf(html, "function handleFolderTreeContextMenu");
    assert.ok(ctx, "应有 handleFolderTreeContextMenu");
    assert.match(ctx, /treeExpandCollapseLabels/);
    assert.doesNotMatch(
      ctx,
      /sectionKind === 'tags'[\s\S]{0,400}展开全部子文件夹/,
      "标签分区菜单不得写死「展开全部子文件夹」"
    );
  });

  await t.test("2. 排序方式必须是悬停飞出，不能一次全铺开", () => {
    const block = bodyOf(html, "function treeSortMenuBlock");
    assert.ok(block, "应有 treeSortMenuBlock");
    assert.match(block, /eagleMenuFlyout/);
    assert.match(html, /function eagleMenuFlyout/);
    assert.match(html, /#eagleContextMenu \.eagle-menu-flyout/);
    assert.match(html, /orientEagleMenuFlyout/);
  });

  await t.test("3. 试卷右键也走同一套排序飞出，次要动作进「更多」", () => {
    const open = bodyOf(html, "function openEagleContextMenu");
    assert.ok(open, "应有 openEagleContextMenu");
    assert.match(open, /treeSortMenuBlock\(\)/);
    assert.match(open, /eagleMenuFlyout/);
    assert.match(open, /title:\s*['"]更多['"]/);
    assert.doesNotMatch(open, /按创建时间 \(新→旧\)/);
  });

  await t.test("4. 右键菜单允许飞出层溢出，避免被 overflow 裁掉", () => {
    assert.match(
      html,
      /id="eagleContextMenu"[\s\S]{0,280}overflow 必须可见|id="eagleContextMenu"[\s\S]{0,200}(?!overflow-y-auto)/
    );
    assert.doesNotMatch(
      html,
      /id="eagleContextMenu"[^>]*overflow-y-auto/,
      "eagleContextMenu 不得再用 overflow-y-auto 裁飞出层"
    );
  });

  await t.test("5. 题目右键：复制 / 更多 / 排序都走飞出", () => {
    const qmenu = bodyOf(html, "function openQuestionContextMenu");
    assert.ok(qmenu, "应有 openQuestionContextMenu");
    assert.match(qmenu, /eagleMenuFlyout\([\s\S]*title:\s*['"]复制['"]/);
    assert.match(qmenu, /eagleMenuFlyout\([\s\S]*title:\s*['"]更多['"]/);
    assert.match(qmenu, /questionSortMenuBlock\(\)/);
    assert.doesNotMatch(
      qmenu,
      /data-ctx-sep="1"[\s\S]{0,80}复制[\s\S]{0,40}uppercase/,
      "复制不得再作为整段分组标题铺开"
    );
  });

  await t.test("6. 标签右键：筛选与管理收进飞出", () => {
    const ctx = bodyOf(html, "function handleFolderTreeContextMenu");
    assert.ok(ctx, "应有 handleFolderTreeContextMenu");
    assert.match(ctx, /node\.type === 'tag'[\s\S]*eagleMenuFlyout/);
    assert.match(ctx, /title:\s*['"]筛选['"]/);
    assert.match(ctx, /title:\s*['"]管理['"]/);
    assert.match(ctx, /从当前结果排除（含子标签）/);
    assert.match(ctx, /设为多父/);
  });

  await t.test("7. 自定义文件夹 / 智能文件夹次要动作进「更多」飞出", () => {
    const ctx = bodyOf(html, "function handleFolderTreeContextMenu");
    assert.match(ctx, /node\.type === 'userfolder'[\s\S]*title:\s*['"]更多['"]/);
    assert.match(ctx, /node\.type === 'custom'[\s\S]*title:\s*['"]更多['"]/);
    assert.match(ctx, /移动到\.\.\./);
  });
});
