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
assert.match(managerJs, /"标签分组 \(" \+ tagGroups\(\)\.length \+ "\)"/, "左栏列出标签分组并带数量");
assert.match(managerJs, /id = "tagManagerCreateGroup"/, "左栏能新建分组");
assert.match(managerJs, /tag-directory-group-manage/, "分组旁边有管理入口（改名/删除）");

// 1.27.0：「未分类」= 没归到**任何**分组 —— 类型已经变成真分组了，
// 不再有「系统分组不算」这回事。
assert.match(managerJs, /function isUngrouped\(tag\) \{ return !\(tag\.group_ids \|\| \[\]\)\.length; \}/,
  "未分类 = 没有归到任何分组");
assert.match(managerJs, /function tagGroups\(\) \{ return \(state\.catalogue\.groups \|\| \[\]\)\.filter\(\(group\) => !group\.section\); \}/,
  "教材章节那套不当分组显示（那是章节树的事）");

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

// 1.27.0：撤销入口摆在状态条边上，不藏在页面底下的折叠区里。
// 「能做撤销」和「找得到撤销」是两回事 —— 找得到才算。
assert.match(managerJs, /id = "tagManagerUndoLast"/, "状态条边上有撤销按钮");
assert.match(managerJs, /page\.append\(header, toolbar, statusRow, body, history\)/, "状态条和撤销按钮一起摆");
assert.match(managerJs, /state\.lastOperation = result\?\.operation_id \|\| ""/, "每次保存都记下这一步的操作编号");
assert.match(managerJs, /async function undoLastOperation\(\)/, "有撤销上一步的动作");
assert.match(managerJs, /"\/api\/library\/tags\/undo"/, "撤销走服务层的 undo 接口");
assert.match(managerJs, /undoLast\.hidden = !state\.lastOperation/, "没有可撤销的操作时不摆这个按钮");

// 「历史标签」这个词老师看不懂 —— 得说清楚它是什么，而且要**常驻可见**。
// 悬停提示不算说明（这是这个项目一贯的规矩）。
assert.match(managerJs, /tag-directory-group-note", "旧版迁入 · 已停用 · 没贴在题上"/,
  "左栏「历史标签」底下常驻一行说明");
assert.match(managerJs, /legacyNoteBox\.hidden = !legacy/, "选中历史标签时结果区上方有常驻说明");
assert.match(managerJs, /legacyNoteText\.textContent = "这 " \+ legacyGroup\.tag_count \+ " 个是从旧版题库迁进来的标签/,
  "选中历史标签时把这一组说清楚");
// 说不实的话比不说更糟 —— 「不会再被搜到」没核实过（检索并不按 active 过滤），
// 这些标签只是恰好一道题都没贴。
assert.doesNotMatch(managerJs, /不会再被搜到/, "不写没核实过的断言");
assert.match(managerCss, /\.tag-directory-legacy-note \{/, "历史标签的说明有自己的样式");
assert.match(managerCss, /\.tag-directory-group-note \{/, "左栏那行常驻说明有样式");

// 1.27.0：分组只有一种 —— 内置的类型组和老师自建的，是同一种东西。
// 类型变成了真的分组（迁移建 4 个组 + 306 条归属），所以界面上不再有
// 「系统默认分组」那一档，也不再有按 kind 筛的那一档。
assert.match(managerJs, /"标签分组 \(" \+ tagGroups\(\)\.length \+ "\)"/, "左栏只有一档「标签分组」");
assert.doesNotMatch(managerJs, /系统默认分组/, "没有「系统分组」的特殊一档");
assert.doesNotMatch(managerJs, /state\.scope\.startsWith\("kind:"\)/, "不再按类型筛 —— 类型已经是分组了");
assert.match(managerJs, /function tagGroups\(\) \{ return \(state\.catalogue\.groups \|\| \[\]\)\.filter\(\(group\) => !group\.section\); \}/,
  "教材章节那套不当分组显示（那是章节树的事）");
assert.match(managerJs, /function isUngrouped\(tag\) \{ return !\(tag\.group_ids \|\| \[\]\)\.length; \}/,
  "未分类 = 不属于任何分组");
assert.match(managerJs, /group\.system_key === "builtin:legacy"/, "历史标签组按内置标记认，不靠名字");

// ---------------------------------------------------------------- 后端

assert.match(service, /"initial": initial_for\(tag\.name\)/, "接口把拼音首字母给前端");
assert.match(service, /"group_ids": sorted\(str\(value\.pk\) for value in tag\.groups\.all\(\)\)/,
  "接口给的是分组 id —— groups.all() 给的是模型对象，str() 出来前端认不出来");
assert.match(service, /"ungrouped_count": ungrouped_count/, "接口报未分类的数量");
assert.match(initials, /from pypinyin import Style, lazy_pinyin/, "拼音首字母用 pypinyin 算，不自己造表");

console.log("标签管理窗口：左栏四段、首字母分组网格、标签群组增删改归组：OK");
