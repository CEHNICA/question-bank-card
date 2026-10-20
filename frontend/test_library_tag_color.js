"use strict";
// 同一个标签，在几个地方长得一样（1.25.93）。
//
// 老师 10 月 20 日说：「标签系统的界面感觉还是有问题」，随后点明是「整理勾选题的标签」
// 那个窗口，毛病是「同一个标签在几个地方长得不一样，操作也不一样」。
//
// 查下来，根子不在样式，在**颜色只有一处真算了**：
//   · 教材目录：算了 —— 自己设的颜色 → 同型祖先的颜色 → 按类型/学段从调色板取
//   · 标签管理、标签选择窗口、题目详情、题卡：只看了「这颗标签自己有没有设颜色」
// 而老师的标签几乎都没设过颜色（302 个里只有 17 个章设了），于是同一个标签在五个地方
// 是五种颜色：目录里是蓝的、标签管理里是灰绿的、选择窗口里是灰的、详情里是灰的、
// 题卡上一律绿。
//
// 这一版把规则收到共享的 library-workspace.js 里，五处都调它 —— 颜色是**算出来的**，
// 不是各写各的。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { tagColor } = require("./library-workspace.js");
const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const managerJs = fs.readFileSync(path.join(__dirname, "library-tag-manager.js"), "utf8");
const mapJs = fs.readFileSync(path.join(__dirname, "library-tag-map.js"), "utf8");
const libraryCss = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
const mapCss = fs.readFileSync(path.join(__dirname, "library-tag-map.css"), "utf8");
const managerCss = fs.readFileSync(path.join(__dirname, "library-tag-manager.css"), "utf8");

// ── 一、这条规则本身：自己 → 同型祖先 → 按类型/学段 ──────────────
// 先量这条规则算得对不对 —— 下面的断言要拿它的输出当尺子。
assert.equal(tagColor({ id: "a", name: "自己设色的", kind: "knowledge", color: "#AA11BB" }, []), "#aa11bb",
  "标签自己设了颜色，就用它自己的那个");

const catalogue = [
  { id: "chap", name: "初中 · 图形与几何", kind: "knowledge", color: "#477a9e", parent_ids: [] },
  { id: "leaf", name: "几何证明", kind: "knowledge", color: "", parent_ids: ["chap"] },
];
assert.equal(tagColor(catalogue[1], catalogue), "#477a9e",
  "自己没设色的，继承同型祖先的颜色 —— 老师的标签几乎都是这种");

const mixedKinds = [
  { id: "m", name: "方法 · 代数与函数", kind: "method", color: "#8069a3", parent_ids: [] },
  { id: "k", name: "某个知识点", kind: "knowledge", color: "", parent_ids: ["m"] },
];
assert.notEqual(tagColor(mixedKinds[1], mixedKinds), "#8069a3",
  "不跨类型找祖先 —— 知识点不继承解题方法的颜色");
assert.equal(tagColor(mixedKinds[1], mixedKinds), "#5b6770",
  "它自己没学段、也没设色，就按默认色走");

assert.equal(tagColor({ id: "c", name: "不会", kind: "custom", color: "", parent_ids: [] }, []), "#8965a5",
  "自定义标签按调色板取色");
assert.equal(tagColor({ id: "h", name: "旧标签", kind: "legacy", color: "", parent_ids: [] }, []), "#5b6770",
  "历史标签也取得到色 —— 以前调色板里没有这一类，这一支会读到空");
assert.equal(tagColor({ id: "g", name: "高中 · 函数", kind: "knowledge", color: "", parent_ids: [] }, []), "#387f72",
  "名字里带「高中」的按高中那个色，初中同理");
assert.equal(tagColor(null, catalogue), "", "没有标签时不硬凑一个颜色");
assert.equal(tagColor({ id: "x", name: "某个标签", kind: "knowledge", color: "", parent_ids: [] }, []), "#5b6770",
  "谁都不沾的按默认色，不会留下一颗没颜色的灰标签");

// ── 一之二、回退色不许跟老师自己设的颜色反着来 ────────────────
// 老师给初中章设的是蓝 #477a9e、高中章设的是绿 #387f72、方法章设的是紫 #8069a3。
// 1.25.93 实机复验时发现，早先的回退调色板写的是「初中绿、高中蓝」—— 正好拧着来：
// 「平面向量的应用」既没有上级、又同时属于初中和高中，掉进回退色，
// 在 98 颗蓝的初中知识点里成了唯一一颗绿。
const teacher初中 = "#477a9e";
const teacher高中 = "#387f72";
const teacher方法 = "#8069a3";
assert.equal(tagColor({ id: "o", name: "初中 · 方程", kind: "knowledge", color: "", parent_ids: [] }, []), teacher初中,
  "初中回退色 = 老师给自己初中章设的那个色");
assert.equal(tagColor({ id: "o2", name: "高中 · 函数", kind: "knowledge", color: "", parent_ids: [] }, []), teacher高中,
  "高中回退色 = 老师给自己高中章设的那个色");
assert.equal(tagColor({ id: "o3", name: "方法 · 几何", kind: "method", color: "", parent_ids: [] }, []), teacher方法,
  "解题方法的回退色 = 老师给自己方法章设的那个色");

// 而且不该只靠写死的那一份：找不到上级时，先去问同学段同类型里那些顶级的章是什么色，
// 跟着它们走。老师改了某章的颜色，底下那颗标签也跟着变。
const orphan = [
  { id: "c1", name: "初中 · 数与式", kind: "knowledge", color: "#123456", parent_ids: [], levels: ["初中"] },
  { id: "c2", name: "高中 · 函数与方程", kind: "knowledge", color: "#654321", parent_ids: [], levels: ["高中"] },
  { id: "orphan", name: "平面向量的应用", kind: "knowledge", color: "", parent_ids: [], levels: ["初中", "高中"] },
];
assert.equal(tagColor(orphan[2], orphan), "#123456",
  "没有上级的初中标签，跟初中那几颗顶级章走 —— 不会在满屏蓝里冒出一颗绿");
assert.equal(tagColor({ id: "x2", name: "高中 · 立体几何", kind: "knowledge", color: "", parent_ids: [], levels: ["高中"] }, orphan), "#654321",
  "高中那颗跟高中那几颗顶级章走");
assert.equal(tagColor({ id: "m3", name: "方法 · 统计", kind: "method", color: "", parent_ids: [], levels: ["初中", "高中"] }, orphan), teacher方法,
  "方法那种没学段的顶级章只有一种颜色时才跟着它走");
assert.equal(tagColor({ id: "amb", name: "初中 · 杂项", kind: "knowledge", color: "", parent_ids: [], levels: ["初中"] }, [
  { id: "a1", name: "初中 · 甲", kind: "knowledge", color: "#111111", parent_ids: [], levels: ["初中"] },
  { id: "a2", name: "初中 · 乙", kind: "knowledge", color: "#222222", parent_ids: [], levels: ["初中"] },
]), "#477a9e",
  "同类型的顶级章颜色不一致时不乱猜，回到老师那一份回退色");
assert.equal(tagColor({ id: "nm", name: "没学段也没上级", kind: "knowledge", color: "", parent_ids: [] }, orphan), "#5b6770",
  "既没学段、又找不到章的，走默认色");

// 同一颗标签，问几次都得是同一个答案 —— 否则五处各算一次还是会对不上。
for (const tag of [...catalogue, ...mixedKinds]) {
  assert.equal(tagColor(tag, catalogue.concat(mixedKinds)), tagColor(tag, catalogue.concat(mixedKinds)),
    `「${tag.name}」的颜色每次问都一样`);
}

// ── 二、五处都调这同一个函数 ───────────────────────────────────
// 谁自己另算一遍，就又会跟别处不一样。这是这一版的全部要害。
//
// 关键是**逐处**验，不是数一数有几处调用 —— 只数次数的话，改坏其中一处而别处照旧，
// 次数不变，测试照样绿（这坑真踩过：把题卡那处退回老写法，计数型断言毫无反应）。
// 所以这里按函数切片，逐个函数验它里面到底调没调共享的那一个。
function sliceFunction(source, name) {
  const start = source.indexOf(`\n  function ${name}(`);
  if (start === -1) return "";
  const rest = source.slice(start + 1);
  const next = rest.slice(1).search(/\n {2}(async )?function \w+\(/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}

const surfaces = [
  ["题卡上那颗标签", sliceFunction(libraryJs, "tagHoverChip")],
  ["题目详情里那颗标签", sliceFunction(libraryJs, "extrasNode")],
  ["标签选择窗口里那几颗已贴上的", sliceFunction(libraryJs, "renderTagPickerNow")],
  ["标签选择窗口里那一行", sliceFunction(libraryJs, "renderTagPicker")],
  ["教材目录那一行", sliceFunction(libraryJs, "directoryColor")],
  ["更多筛选里那一颗", sliceFunction(libraryJs, "tagChoice")],
];
for (const [name, source] of surfaces) {
  assert.ok(source, `能定位到「${name}」那段代码 —— 改名了要改这里`);
  assert.match(source, /workspace\.tagColor\(/, `「${name}」调的是共享的那个颜色函数，不是自己算`);
}

// 这两处在另外两个文件里，各自用自己那侧的写法。
assert.match(managerJs, /root\.LibraryWorkspace\.tagColor\(tag, state\.catalogue\.tags \|\| \[\]\)/,
  "标签管理列表的圆点调同一个颜色函数");
assert.match(mapJs, /root\.LibraryWorkspace\.tagColor\(tag, state\.catalogue\.tags \|\| \[\]\)/,
  "标签地图上的标签也调同一个颜色函数");

// 目录那一处是包了一层（directoryColor），它也必须转给共享函数。
assert.match(libraryJs, /function directoryColor\(tag\) \{ return workspace\.tagColor\(tag, directory\.tags\); \}/,
  "教材目录那一条也是同一个规则，不是自己算的");

// 目录里「添加标签」的候选列表、筛选条上那颗标签，也要用同一个。
assert.match(libraryJs, /workspace\.tagColor\(entry\.tag, tagCatalogue\.tags \|\| \[\]\)/,
  "目录里候选标签的圆点也用同一个颜色");
assert.match(libraryJs, /workspace\.tagColor\(t, tagCatalogue\.tags \|\| \[\]\)/,
  "筛选条上那颗标签也用同一个颜色");

// ── 三、老写法不许回来 ─────────────────────────────────────────
// 「只看这颗标签自己有没有设颜色」就是这次的病根。它要是再冒出来，同一个标签立刻
// 又会跟别处不是一个颜色。
for (const [name, source] of [["library.js", libraryJs], ["library-tag-manager.js", managerJs], ["library-tag-map.js", mapJs]]) {
  assert.doesNotMatch(source, /setProperty\("--tag-color",\s*[^;]*\.color\b/,
    `${name}：不许再拿标签自己那个可能为空的颜色直接上色`);
  assert.doesNotMatch(source, /\.style\.backgroundColor = [^;]*\.color\b/,
    `${name}：圆点也不许再只认标签自己设的颜色`);
  assert.doesNotMatch(source, /\.color\s*&&\s*\/\^#/,
    `${name}：不许再出现「标签自己设了颜色才上色」这种写法`);
}

// ── 四、形状也统一：列表里的一颗标签 = 彩色圆点 + 名字 ───────────
// 圆点 = 列表里的一颗标签（标签管理、教材目录的子标签、标签选择窗口的行、标签地图）；
// 色条 = 一个章节（教材目录的章根）。
for (const [name, css, selector] of [
  ["标签管理列表", managerCss, /\.tag-directory-color \{[^}]*border-radius: 50%/],
  ["教材目录的子标签", libraryCss, /\.library-directory-dot \{[^}]*border-radius: 50%/],
  ["标签选择窗口", libraryCss, /\.tag-picker-dot \{[^}]*border-radius: 50%/],
  ["标签地图", mapCss, /\.tag-map-dot \{[^}]*border-radius: 50%/],
  ["更多筛选里那一颗", libraryCss, /\.library-tag-choice-dot \{[^}]*border-radius: 50%/],
]) {
  assert.match(css, selector, `${name}：一颗标签画成彩色圆点 —— 各处长得一样`);
}

// 每一颗圆点都得上那颗标签自己的颜色。
for (const [name, css, selector] of [
  ["教材目录的子标签", libraryCss, /\.library-directory-dot \{[^}]*var\(--directory-color/],
  ["标签选择窗口", libraryCss, /\.tag-picker-dot \{[^}]*var\(--tag-color/],
  ["标签地图", mapCss, /\.tag-map-dot \{[^}]*var\(--tag-color/],
]) {
  assert.match(css, selector, `${name}：圆点的颜色是这颗标签自己的`);
}

// ── 五、贴在题上的那颗标签：题卡和详情得是同一种 ───────────────
// 题卡上原来一律绿（accent-soft），详情里原来一律灰（没上 --tag-color 时的兜底色）。
// 现在两处都用标签自己的颜色：淡底 + 同色边。
assert.match(libraryCss, /\.library-card-tag-glimpse \{[^}]*var\(--tag-color/,
  "题卡上那颗标签用标签自己的颜色，不是固定的一律绿");
assert.match(libraryCss, /\.library-tag-chip \{[^}]*var\(--tag-color/,
  "详情里那颗标签用的是同一个变量");
assert.doesNotMatch(libraryCss, /\.library-card-tag-glimpse \{[^}]*background: var\(--accent-soft\)/,
  "题卡上那颗标签不许再是一律绿");

// ── 六、选择窗口里的话是人话 ───────────────────────────────────
// 原来那三句「全部都有／部分有 2/3／全部没有」说的是「这个标签」怎么样，
// 老师读起来会以为库里没这个标签。改成说「这几道题」怎么样。
assert.match(libraryJs, /" 道里都没有"/, "多选时说的是「这几道题」里都没有");
assert.match(libraryJs, /" 道里有 " \+ count \+ " 道"/, "部分有的时候说清是几道里有几道");
assert.match(libraryJs, /" 道都有"/, "全都有的时候直接说几道都有");
assert.doesNotMatch(libraryJs, /"全部没有"|"部分有 "/,
  "旧的三句不许回来 —— 说的是标签，不是这几道题");

// 左栏原来把二十来个顶层标签平铺着排，知识点和解题方法混在一起，只能靠名字前缀去分。
assert.match(libraryJs, /\["knowledge", "method", "custom", "legacy"\]\.forEach/,
  "左栏按类型分组，知识点和解题方法不再混着排");
assert.match(libraryJs, /node\("span", "tag-picker-group-title"/,
  "每一组有个小标题");
assert.match(libraryCss, /\.tag-picker-group-title \{/, "小标题有样子");

console.log("One tag, one colour: the catalogue, the manager, the picker, the card and the panel "
  + "all read the same shared rule: OK");
