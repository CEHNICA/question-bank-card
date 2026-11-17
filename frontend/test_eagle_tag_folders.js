"use strict";
// Eagle：标签作为特殊文件夹 —— 静态结构与关键交互落点检查
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

assert.match(html, /section:tags/, "主树包含 section:tags");
assert.match(html, /sectionKind:\s*'tags'/, "标签分区 sectionKind=tags");
assert.match(html, /promptCreateTreeTag/, "支持树内新建标签");
assert.match(html, /assignTagToQuestions/, "支持拖题贴标");
assert.match(html, /reparentTagByDrop/, "支持标签拖拽改归属");
assert.match(html, /e\.altKey/, "Alt 多父级");
assert.match(html, /add_parent/, "走 catalogue add_parent");
assert.match(html, /move_parent/, "走 catalogue move_parent");
assert.match(html, /create_tag/, "走 catalogue create_tag");
assert.match(html, /confirmDeleteTreeTag/, "右键删除标签");
assert.match(html, /tags\/impact/, "删除前走 impact 预览");
assert.doesNotMatch(html, /展开标签地图/, "侧栏底栏不得再放标签地图按钮");
assert.doesNotMatch(
  html,
  /title="打开标签管理中心"/,
  "侧栏底栏不得再放「管理」按钮；标签管理只走 ≡ 主菜单"
);
assert.match(html, /openTagManagerModal\(\);\s*hideEagleAppMainMenu\(\);/, "≡ 主菜单保留标签管理中心");
{
  // 左树：标签分区要紧挨「最近浏览」下面，别沉到文件夹后面。
  const rootsFn = html.match(/function getFolderTreeRoots\(\)[\s\S]*?\n    function collectTagIds/);
  assert.ok(rootsFn, "应能找到 getFolderTreeRoots");
  const recentAt = rootsFn[0].indexOf("smart:recent");
  const tagsAt = rootsFn[0].indexOf("section:tags");
  const foldersAt = rootsFn[0].indexOf("section:folders");
  assert.ok(recentAt >= 0 && tagsAt > recentAt, "标签分区应在最近浏览之后");
  assert.ok(foldersAt > tagsAt, "文件夹分区应在标签之后");
}
{
  // ≡ 菜单：标签管理中心放在最近浏览下面。
  const menu = html.match(/id="eagleAppMainMenu"[\s\S]*?id="imageLightboxModal"/);
  assert.ok(menu, "应能找到 ≡ 主菜单");
  const recentAt = menu[0].indexOf("smart:recent");
  const tagMgrAt = menu[0].indexOf("openTagManagerModal()");
  assert.ok(recentAt >= 0 && tagMgrAt > recentAt, "≡ 菜单里标签管理应在最近浏览下面");
}
assert.doesNotMatch(html, /所属标签胶囊/, "标签不得混进检查器文件夹区");
assert.match(html, /targetNode\.type === 'tag'/, "拖放目标允许标签节点");
assert.match(html, /folderType === 'tag'/, "拖拽载荷支持 tag");
assert.match(html, /tags\/assignments\/batch/, "批量贴标走 batch 接口");
assert.match(html, /collectTagIds\(node\)/, "点选标签写入 folderTagIds 用 collectTagIds");
assert.match(html, /sortFolderTreeNodes/, "标签树复用文件夹排序");
assert.match(html, /countQuestionsForTagIdSet/, "标签计数按去重题数");
assert.match(html, /subtree_count/, "标签计数对齐目录 subtree_count");
assert.doesNotMatch(
  html,
  /roots\.sort\(\(a, b\) => b\.count - a\.count/,
  "不得再写死按题量排序标签根"
);
assert.doesNotMatch(
  html,
  /知识点 \/ 方法已挪到左栏底固定面板/,
  "旧「知识点单独底栏」注释应已移除"
);
assert.match(
  html,
  /function renderKnowledgePanel\(\)\s*\{\s*return;\s*\}/,
  "renderKnowledgePanel 已退化为空操作"
);
assert.match(html, /group:knowledge/, "标签分区含知识点根");
assert.match(html, /group:method/, "标签分区含解题方法根");

console.log("Eagle tag-as-folder static checks: OK");
