"use strict";

// 1.26.39：控件话术集中在一处以后，得盯住两件事：
//   1) 话术表本身没说空话（「为什么灰、下一步去哪」都写到了）；
//   2) 页面上的字、悬停说明、脚本引用顺序，都跟这张表一致 ——
//      两边各写一套，这张表就白做了。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const W = require("./control-wording.js");
const libraryHtml = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const indexHtml = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

// ── 一、话术表本身 ──────────────────────────────────────────
for (const [name, wording] of Object.entries(W.INSPECTOR)) {
  assert.ok(wording && wording.label && wording.hint, `${name} 的说法不能是空的`);
  assert.ok(wording.label.length <= 8, `${name} 的按钮文字要短：${wording.label}`);
}
assert.match(W.INSPECTOR.disabled.hint, /先在左边点一道题/, "灰掉的按钮要写清下一步去哪");
assert.match(W.INSPECTOR.expanded.hint, /点左边的题卡/, "收起之后要说清怎么再打开");
assert.match(W.INSPECTOR.collapsed.hint, /标签和待核对建议/, "展开之后要说清能看到什么");
assert.match(W.DIRECTORY.hint, /「目录」/, "收起目录要说清再从哪儿打开");
assert.match(W.CROP.hint, /从原卷裁图/, "收起裁剪条要说清再从哪儿打开");
for (const [name, wording] of Object.entries(W.SOURCE)) {
  assert.ok(wording.label && wording.hint, `${name} 的说法不能是空的`);
}

// 面板里那颗和顶栏那颗是同一个操作 —— 必须叫同一个名字。
assert.equal(W.INSPECTOR.expanded.label, "收起详情");
assert.equal(W.INSPECTOR.collapsed.label, "展开详情");
assert.notEqual(W.INSPECTOR.expanded.label, W.INSPECTOR.collapsed.label, "开着和收着不能叫同一个名字");

// 「更多」的说明按菜单里实际有的项生成。
assert.equal(W.menuSummaryTitle([]), "更多操作");
assert.equal(W.menuSummaryTitle(["修改题目", "撤回"]), "更多操作：修改题目、撤回");
assert.equal(W.menuSummaryTitle(["修改题目", "修改题目"]), "更多操作：修改题目", "重复项不重复列");
assert.equal(W.menuSummaryTitle(["  撤回  "]), "更多操作：撤回", "名字前后空格要擦掉");
assert.equal(W.menuSummaryTitle(["修改题目", ""]), "更多操作：修改题目", "空项不列");
assert.equal(W.menuSummaryTitle(null), "更多操作");
assert.doesNotMatch(W.menuSummaryTitle(["修改题目"]), /查看识读记录|框选识读/, "1.26.33 撤掉的入口不许再出现在说明里");

// ── 二、页面上的字跟话术表一致 ──────────────────────────────
function buttonById(html, id) {
  const match = html.match(new RegExp(`<button[^>]*id="${id}"[^>]*>`));
  assert.ok(match, `页面里还有 id="${id}" 这颗按钮`);
  return match[0];
}

const closeInspector = buttonById(libraryHtml, "closeInspector");
assert.ok(libraryHtml.includes(`${closeInspector}${W.INSPECTOR.expanded.label}</button>`),
  "面板里那颗按钮的字要跟话术表一样");
assert.ok(closeInspector.includes(`title="${W.INSPECTOR.expanded.hint}"`),
  "面板里那颗按钮的悬停说明要跟话术表一样");

for (const [id, key] of [["sourceQuestion", "question"], ["sourceWholePage", "wholePage"], ["sourceFit", "fit"]]) {
  const tag = buttonById(libraryHtml, id);
  assert.ok(tag.includes(`title="${W.SOURCE[key].hint}"`), `${id} 的悬停说明要跟话术表一样`);
}

// ── 三、脚本引入顺序 ────────────────────────────────────────
assert.match(libraryHtml, /<script src="\/control-wording\.js" defer><\/script>/,
  "题库页要引话术文件");
assert.match(indexHtml, /<script src="\/control-wording\.js" defer><\/script>/,
  "核对页要引话术文件");
assert.match(libraryHtml, /<script src="\/control-wording\.js" defer><\/script>\s*<script src="\/library-answer-editor\.js"/,
  "话术文件必须排在用它的人前面（defer 按书写顺序执行）");
assert.match(libraryHtml, /<script src="\/control-wording\.js" defer><\/script>[\s\S]*?<script src="\/library\.js"/,
  "话术文件必须排在题库主脚本前面");
assert.match(indexHtml, /<script src="\/control-wording\.js" defer><\/script>[\s\S]*?<script src="\/app\.js"/,
  "话术文件必须排在核对页主脚本前面");

// ── 四、后端得真发得出这个文件 ────────────────────────────
const urls = fs.readFileSync(path.join(__dirname, "..", "backend", "qb_server", "urls.py"), "utf8");
assert.match(urls, /path\("control-wording\.js", views\._frontend\("control-wording\.js", "application\/javascript; charset=utf-8"\)\)/,
  "后端少了这条路由，页面就拿不到话术文件 —— 界面会退回到旧样子");

// 话术模块得挂到 window 上 —— 页面全靠它取话术。
const moduleSource = fs.readFileSync(path.join(__dirname, "control-wording.js"), "utf8");
assert.match(moduleSource, /root\.QBControlWording = value/, "话术模块要挂到 window 上");

console.log("control wording: ok");
