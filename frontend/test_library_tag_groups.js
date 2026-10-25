/* 标签群组 + 首字母分组网格：界面契约
 *
 * 1.27.0：老师要的样子 ——
 *   左栏：全部 / 未分类 / 常用标签 / 标签群组(N)
 *   右侧：按拼音首字母分组，组内平铺成网格，每颗标签带题数
 *
 * 这份测试盯住界面契约（源码文本断言），跟这个项目其余前端测试一个路子。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const managerJs = fs.readFileSync(path.join(__dirname, "library-tag-manager.js"), "utf8");
const managerCss = fs.readFileSync(path.join(__dirname, "library-tag-manager.css"), "utf8");
const service = fs.readFileSync(path.join(__dirname, "..", "backend", "core", "library_tagging.py"), "utf8");
const initials = fs.readFileSync(path.join(__dirname, "..", "backend", "core", "pinyin_initials.py"), "utf8");

// ---------------------------------------------------------------- 左栏

assert.match(managerJs, /entry\(scopeNav, "all", "全部"/, "左栏第一项是「全部」");
assert.match(managerJs, /entry\(scopeNav, "ungrouped", "未分类"/, "左栏有「未分类」");
assert.match(managerJs, /entry\(scopeNav, "favorite", "常用标签"/, "左栏有「常用标签」");
assert.match(managerJs, /"标签群组 \(" \+ tagGroups\(\)\.length \+ "\)"/, "左栏列出标签群组并带数量");
assert.match(managerJs, /id = "tagManagerCreateGroup"/, "左栏能新建分组");
assert.match(managerJs, /tag-directory-group-manage/, "分组旁边有管理入口（改名/删除）");

// 「未分类」是按「没归到任何**老师自建分组**」算的 —— 系统分组（教材章节那套）
// 不算，每个标签都挂在某个章节下，说它「没归类」是骗人。
assert.match(managerJs, /function isUngrouped\(tag\) \{[\s\S]*?mine\.has\(String\(id\)\)/,
  "未分类 = 没有归到任何自建分组");
assert.match(managerJs, /function tagGroups\(\) \{ return \(state\.catalogue\.groups \|\| \[\]\)\.filter\(\(group\) => !group\.system\); \}/,
  "系统分组不在「标签群组」里重复列一遍");

// ---------------------------------------------------------------- 右侧网格

assert.match(managerJs, /tag-directory-letter-group/, "右侧按首字母分组");
assert.match(managerJs, /tag-directory-letter-heading/, "每组有组头");
assert.match(managerJs, /tag-directory-grid/, "组内是网格");
assert.match(managerJs, /tag\.initial/, "分组字母用后端给的拼音首字母");
assert.match(managerJs, /tag-directory-chip-count/, "每颗标签带题数");
assert.match(managerCss, /grid-template-columns: repeat\(auto-fill, minmax\(190px, 1fr\)\)/,
  "网格每列 190 px 起 —— 1160 px 窗口下正好 4 列，标签名不被截断");

// 「#」组排在最后 —— 认不出首字母的不假装它属于哪个字母。
assert.match(managerJs, /\(a === "#" \? 1 : b === "#" \? -1 : a\.localeCompare\(b\)\)/, "认不出的排在最后");

// 勾选框要一直看得见 —— 批量归组就靠它。只在悬停时显形等于把入口藏起来了。
assert.match(managerCss, /\.tag-directory-chip-check \{[^}]*opacity: \.4;/,
  "勾选框常驻，只是压得很淡");
assert.match(managerCss, /@media \(hover: none\) \{ \.tag-directory-chip-check \{ opacity: 1; \} \}/,
  "触屏上勾选框一直是实的");

// ---------------------------------------------------------------- 群组操作

assert.match(managerJs, /"create_group"/, "能建组");
assert.match(managerJs, /"update_group"/, "能改名");
assert.match(managerJs, /"delete_group"/, "能删组");
assert.match(managerJs, /"group_membership"/, "能归组/移出组");
assert.match(managerJs, /"group_membership_batch"/, "能一次归一组");
assert.match(managerJs, /所属分组/, "详情里说清楚这颗标签在哪些组");
assert.match(managerJs, /删除分组「" \+ group\.name/, "删组前说清楚标签还在，只是回到未分类");

// 操作记录里群组操作要有名字 —— 一律显示「标签操作」等于没写。
assert.match(managerJs, /"catalogue:create_group": "新建标签群组"/, "操作记录里有「新建标签群组」");
assert.match(managerJs, /"catalogue:delete_group": "删除标签群组"/, "操作记录里有「删除标签群组」");
assert.match(managerJs, /"catalogue:group_membership_batch": "批量调整标签所属分组"/, "操作记录里有「批量调整标签所属分组」");

// ---------------------------------------------------------------- 后端

assert.match(service, /"initial": initial_for\(tag\.name\)/, "接口把拼音首字母给前端");
assert.match(service, /"group_ids": sorted\(str\(value\.pk\) for value in tag\.groups\.all\(\)\)/,
  "接口给的是分组 id —— groups.all() 给的是模型对象，str() 出来前端认不出来");
assert.match(service, /"ungrouped_count": ungrouped_count/, "接口报未分类的数量");
assert.match(initials, /from pypinyin import Style, lazy_pinyin/, "拼音首字母用 pypinyin 算，不自己造表");

console.log("标签管理窗口：左栏四段、首字母分组网格、标签群组增删改归组：OK");
