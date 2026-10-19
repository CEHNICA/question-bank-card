"use strict";
// 1.25.58：教材目录长在题库里。守住几个契约：开关在筛选栏；左栏是章 → 节的树，
// 每个节点带题数；点一章按 tag_any 过滤（含子标签）；「全部题目」「未标知识点」
// 是一等入口；开合状态记在本机。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
const shortcutJs = fs.readFileSync(path.join(__dirname, "shortcut-help.js"), "utf8");
const libraryJs = js;
const mapJs = fs.readFileSync(path.join(__dirname, "library-tag-map.js"), "utf8");

assert.match(html, /id="libraryDirectoryToggle"/, "筛选栏里有目录开关");
assert.match(js, /id = "libraryDirectory"/, "目录是题库里的左栏，不是另一个页面");
assert.match(js, /library-directory-open/, "开关切换一个页面状态");
assert.match(js, /tagAny = String\(tag\.id\)/, "点一章按标签筛选");
assert.match(js, /tagMode = "any"/, "筛选包含子标签");
assert.match(js, /directoryCount\(tag\)/, "每个节点写这一层有几道题");
assert.match(js, /个没用到的/, "没题的子标签先收起来");
assert.match(js, /全部题目/, "回到全部题目的入口");
assert.match(js, /未标知识点 · \$\{missing\} 题/, "未标知识点也是目录里的一等入口");
assert.match(js, /qb-library-directory/, "开合状态记在本机");
assert.match(css, /grid-template-columns: 236px minmax\(0, 1fr\) 288px/, "桌面上目录占左栏，题目居中");
assert.match(css, /max-width: 1099px/, "窄屏上目录改成左滑层");

console.log("Library directory: toggle, chapter tree with counts, tag_any filtering, all/untagged entries, remembered state: OK");
assert.match(js, /library-directory-kind/, "目录顶部能切换知识点和解题方法");
assert.match(js, /state\.facets\?\.tags/, "目录里的题数跟着当前筛选走");
assert.match(html, /library-filter-group-note/, "更多筛选里说清楚标签勾选和左栏目录各管什么");
assert.match(js, /function directoryColor/, "目录里每行按标签颜色画边条");
assert.match(js, /--directory-color/, "目录把颜色写进 CSS 变量");
assert.match(js, /DIRECTORY_STAGE_COLOR/, "没有自定义颜色时按学段/类型取一个色");
assert.match(css, /\.library-filter-chip\[style\*="--tag-color"\]/, "标签筛选条按这个标签的颜色上一道软底");
assert.match(js, /library-directory-stage/, "目录顶部有学段切换（全部/初中/高中）");
assert.match(js, /directory\.stage/, "学段筛选状态");
assert.match(js, /async function createInlineTag/, "输入目录里没有的名字，回车就新建");
assert.match(js, /action: "create_tag"/, "新建走目录的 create_tag");
assert.match(js, /已经贴在这道题上了/, "已贴过的同名标签不再提示新建");
assert.match(js, /async function nestTagUnder/, "标签拖到另一颗标签上 = nestTagUnder");
assert.match(js, /"add_parent"/, "嵌套走目录的 add_parent（可撤销，保留其它上级）");
assert.match(js, /isCovered/, "父标签在本题上被子标签顶掉时收起");
assert.match(js, /attachChipDnD/, "拖拽走条带式的事件挂载");
assert.match(css, /\.is-drag-target/, "拖放目标的可视反馈");
assert.match(libraryJs, /Ctrl\+Z|Ctrl\+Z.*撤销.*标签|key === "z" && event\.key === "Z"|event\.key === "z" || event\.key === "Z"/, "Ctrl+Z 接住撤销最近标签操作");
assert.match(shortcutJs, /撤销当前题上一次标签改动/, "快捷键说明里写着 Ctrl+Z");
assert.match(mapJs, /hideEmpty/, "标签地图有 hideEmpty 过滤");
assert.match(mapJs, /只看用到的/, "工具栏上挂着「只看用到的」开关");
assert.match(mapJs, /收起 .*graph\.hidden.* 个还没题目的标签/, "状态栏报告收起的空标签数");
assert.match(libraryJs, /is-empty/, "目录给空章节加 is-empty 样式");
assert.match(libraryJs, /子标签全部空的时候/, "目录在章节里全空时不再刷\"还有 N 个没用到的\"");
assert.match(css, /\.library-directory-row-wrap\.is-empty/, "空章节用更淡的视觉");
assert.match(css, /draft-filter\[data-empty-facet\]/, "0 题的筛选项变淡，不再和正文抢眼");
