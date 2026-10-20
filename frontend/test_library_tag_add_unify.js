"use strict";
// 「加标签」这两处（单题右栏、批量窗口）是同一件事 —— 得长得一样、用起来一样。（1.25.94）
//
// 老师 10 月 20 日晚上 23 点说：
// 「这个整理勾选的标签这个按钮你点进去啊，它是可以添加标签的……这种添加标签的方式跟在右边
//   添加标签的方式是不一样的……这说实话有点有点有点乱了。」
//
// 查下来两边确实差得很远：
//   · 单题右栏「＋添加标签」：展开后只有一个输入框，**不打字就一片空白**，像坏了；
//     候选是「圆点 + 名字 + 上级路径」，最多 8 条。
//   · 批量窗口：打开就铺 263 条，每行还带「知识点 · 初中 › 方程与不等式」和一段说明；
//     新建标签的入口在右上角一个孤零零的按钮上。
//
// 同一件事、两套操作习惯 —— 这一版让两边按同一套来：
//   1. 候选行长成同一副样子：彩色圆点 + 名字 + 上级路径（路径不带类型前缀了）；
//   2. 单题空着不打字时，列出「最近用过的」，不再给一个空盒子；
//   3. 批量窗口打开先列「最近用过的 + 收藏」，不铺满两百多条；
//   4. 新建标签统一放在列表最后一条：「新建「XXX」并贴上」；
//   5. 提示语统一成同一句：「点一下贴上，再点一下拿掉。」
//
// 这一版量的是**真跑出来的两棵 DOM**：把两处渲染函数切出来在假浏览器里跑一遍，
// 再看它们画出来的东西是不是同款 —— 不比对规则的文字。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");

// ---- 把要验的函数从源码里切出来 -------------------------------------------
function sliceFunction(source, name) {
  const start = source.indexOf(`\n  function ${name}(`);
  if (start === -1) return "";
  const rest = source.slice(start + 1);
  const next = rest.slice(1).search(/\n {2}(async )?function \w+\(/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}
const slice = (name) => sliceFunction(libraryJs, name);

const PIECES = [
  "tagChildrenIndex", "tagSubtree", "tagTrail", "tagPathText", "tagRowTitle", "recentTagsForAdd",
  "flowTagById", "flowRecentTags", "takenTagIds", "inlineTagRank", "flowTagPath", "tagAncestorPaths",
  "inlineAddMatches", "buildInlineAddPanel", "renderTagPicker",
].map(name => {
  const code = slice(name);
  assert.ok(code, `能定位到「${name}」那段代码 —— 改名了要改这里`);
  return code;
}).join("\n");

// ---- 假浏览器 -------------------------------------------------------------
class Element {
  constructor(tag, cls = "", text = "") {
    this.tagName = tag; this.className = cls; this.textContent = text;
    this.children = []; this.hidden = false; this.dataset = {}; this.disabled = false;
    this.style = { values: {}, setProperty(name, value) { this.values[name] = value; } };
    this.listeners = {}; this.scrollTop = 0; this.scrollLeft = 0; this.id = "";
    this.classList = {
      add: (...names) => { this.className = [this.className, ...names].filter(Boolean).join(" "); },
      contains: (name) => String(this.className || "").split(" ").includes(name),
    };
  }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute() {}
  focus() {}
  querySelector(selector) {
    const want = String(selector).replace(/^\./, "");
    const stack = [...this.children];
    while (stack.length) {
      const el = stack.shift();
      if (!el || typeof el === "string") continue;
      if (String(el.className || "").split(" ").includes(want)) return el;
      stack.push(...el.children);
    }
    return null;
  }
}
function textOf(element) {
  if (element === null || element === undefined) return "";
  if (typeof element === "string") return element;
  return [element.textContent || "", ...element.children.map(textOf)].join(" ").replace(/\s+/g, " ").trim();
}
function walk(element, matches, out = []) {
  if (!element || typeof element === "string") return out;
  if (matches(element)) out.push(element);
  element.children.forEach(child => walk(child, matches, out));
  return out;
}

// 三颗标签：两颗是初中（其中一颗有上级），一颗是高中。
// 老师的标签几乎都没设过颜色，颜色规则走共享的那一份（1.25.93）。
const TAGS = [
  { id: "chap", name: "初中 · 图形与几何", kind: "knowledge", color: "#477a9e", levels: ["初中"], parent_ids: [], description: "平面图形那几块" },
  { id: "leaf", name: "全等三角形", kind: "knowledge", color: "", levels: ["初中"], parent_ids: ["chap"], description: "形状大小都一样的三角形" },
  { id: "other", name: "高中 · 函数与方程", kind: "knowledge", color: "#387f72", levels: ["高中"], parent_ids: [], description: "" },
  { id: "method", name: "方法 · 数形结合", kind: "method", color: "#8069a3", levels: ["初中", "高中"], parent_ids: [], description: "" },
];

function makeContext(extra = {}) {  const list = new Element("div", "tag-picker-list");
  const groups = new Element("nav", "tag-picker-groups");
  const quick = new Element("nav", "tag-picker-quick");
  const search = new Element("input", "tag-picker-search");
  const store = { "qb-library-recent-tags": JSON.stringify(["leaf", "other"]) };
  const context = vm.createContext({
    node: (tag, cls = "", text = "") => new Element(tag, cls, text),
    $: (id) => ({
      tagPickerList: list, tagPickerGroups: groups, tagPickerQuick: quick, tagPickerSearch: search,
      tagPickerNow: new Element("div", "tag-picker-now"), tagPickerStatus: new Element("span"),
      tagPickerUndo: new Element("button"), tagPickerRetry: new Element("button"),
      tagPickerTitle: new Element("h2"), tagPickerSubtitle: new Element("p"),
    }[id] || null),
    localStorage: { getItem: (key) => (key in store ? store[key] : null), setItem: (key, value) => { store[key] = value; } },
    // 「最近用过的」存在这个键下（flowRecentTags 读的就是它）。
    TAG_FLOW_RECENT: "qb-library-recent-tags",
    workspace: require("./library-workspace.js"),
    kindLabels: { knowledge: "知识点", method: "解题方法", custom: "自定义", legacy: "历史标签" },
    tagCatalogue: { tags: TAGS },
    tagDialog: { classList: { contains: () => false }, open: true },
    tagTargetIds: ["pub-1", "pub-2"],
    tagMemberships: { "pub-1": new Set(["leaf"]), "pub-2": new Set() },
    tagPickerView: "all", tagActiveSection: "all", tagPickerLanding: true, tagUndoId: "",
    inlineAdd: { open: true, query: "", active: 0, itemId: "pub-1", busy: false },
    renderTagPickerNow: () => {},
    toggleTag: () => {},
    openTagCatalogueEditor: () => {},
    ...extra,
  });
  vm.runInContext(PIECES, context);
  return { context, list, groups, quick, search, store };
}

// 沙箱在 contextify 的时候会把里面的对象**复制**一份给 VM —— 之后从外面改沙箱里的对象，
// VM 里看不见。所以要改 VM 里的状态，得在 VM 里改。
function setInVm(context, code) { vm.runInContext(code, context); }

// ---- 一、批量窗口：打开先落在「最近用过的 + 收藏」，不铺满 --------------------
{
  const { context, list } = makeContext();
  context.renderTagPicker();
  const rows = walk(list, el => String(el.className || "").split(" ").includes("tag-picker-row"));
  const titles = walk(list, el => String(el.className || "").split(" ").includes("tag-picker-group-title"));
  assert.ok(rows.length < 20,
    `批量窗口打开时只摆最近用过的几颗，不铺满全部标签（现在铺了 ${rows.length} 行）`);
  assert.ok(titles.some(t => t.textContent === "最近用过的"),
    "落地页先摆「最近用过的」—— 跟单题那边先摆的是同一份");
  assert.ok(rows.length > 0, "落地页有东西可点，不是空盒子");
  const names = rows.map(row => textOf(row.querySelector(".tag-picker-row-name")));
  assert.ok(names.some(name => name.includes("全等三角形")),
    "最近用过的里有那颗标签");
}

// ---- 二、两处的候选行是同一副样子：圆点 + 名字 + 上级路径 --------------------
{
  const { context, list } = makeContext();
  context.renderTagPicker();
  const rows = walk(list, el => String(el.className || "").split(" ").includes("tag-picker-row"));
  const row = rows.find(el => textOf(el).includes("全等三角形"));
  assert.ok(row, "候选行里有那颗有上级的标签");
  const nameLine = row.querySelector(".tag-picker-row-name");
  assert.ok(nameLine, "候选行有一行名字");
  assert.ok(nameLine.querySelector(".tag-picker-dot"), "名字前有彩色圆点");
  const path = row.querySelector(".tag-picker-row-path");
  assert.ok(path, "候选行写着上级路径");
  assert.equal(textOf(path), "初中 · 图形与几何", "路径写的是它的上级");
  assert.doesNotMatch(textOf(path), /知识点|解题方法/,
    "路径里不再带「知识点 · 」这种类型前缀 —— 类型挪到悬停里，两处才长得一样");
  assert.equal(walk(row, el => String(el.className || "").includes("tag-picker-row-detail")).length, 0,
    "说明文字不再单独占一行 —— 挪到悬停里");
  assert.ok(String(row.title || "").includes("形状大小都一样的三角形"),
    "说明文字挪到鼠标放上去才出现");
  assert.ok(String(row.title || "").includes("知识点"),
    "类型也写在悬停里，信息没丢");
  // 顶级的标签没有上级可写，这时写它是哪一类 —— 两处也一样。
  const topRow = rows.find(el => textOf(el).includes("高中 · 函数与方程"));
  assert.equal(textOf(topRow.querySelector(".tag-picker-row-path")), "知识点",
    "没有上级的，写它是哪一类");
}

// ---- 三、同一颗标签，两处写出来的路径一字不差 --------------------------------
{
  const { context, list } = makeContext();
  context.renderTagPicker();
  const row = walk(list, el => String(el.className || "").split(" ").includes("tag-picker-row"))
    .find(el => textOf(el).includes("全等三角形"));
  const batchPath = textOf(row.querySelector(".tag-picker-row-path"));

  const inline = makeContext();
  const panel = inline.context.buildInlineAddPanel({
    id: "pub-1", tag_assignments: [], tag_revision: 1,
  }).panel;
  const items = walk(panel, el => String(el.className || "").split(" ").includes("library-inline-add-item"));
  assert.ok(items.length > 0,
    "单题那边空着不打字时，列出最近用过的 —— 不再是一片空白（以前像坏了）");
  const inlineRow = items.find(el => textOf(el).includes("全等三角形"));
  assert.ok(inlineRow, "单题那边也列出了同一颗标签");
  assert.ok(inlineRow.querySelector(".library-inline-add-dot"), "单题那行也有彩色圆点");
  const inlinePath = textOf(inlineRow.querySelector(".library-inline-add-path"));
  assert.equal(inlinePath, batchPath, "同一颗标签，两处写出来的上级路径一字不差");
}

// ---- 四、提示语两边是同一句 ------------------------------------------------
{
  const inline = makeContext();
  const panel = inline.context.buildInlineAddPanel({ id: "pub-1", tag_assignments: [], tag_revision: 1 }).panel;
  const hint = walk(panel, el => String(el.className || "").split(" ").includes("library-inline-add-hint"))[0];
  assert.ok(hint && !hint.hidden, "那行提示一直挂着 —— 老师得始终知道「点一下会贴上」");
  assert.match(textOf(hint), /点一下贴上，再点一下拿掉/,
    "单题那边的提示语跟批量窗口一样一句话");
  assert.match(libraryJs, /"点一下贴上，再点一下拿掉。"/,
    "批量窗口用的是同一句提示语");
}

// ---- 五、新建标签统一放在列表最后一条 --------------------------------------
{
  const { context, list, search } = makeContext();
  search.value = "全等";
  context.renderTagPicker();
  const rows = walk(list, el => String(el.className || "").split(" ").includes("tag-picker-row"));
  const last = rows[rows.length - 1];
  assert.ok(last, "列表里有行");
  assert.match(textOf(last), /新建「全等」并贴上/,
    "新建入口在列表最后一条，跟单题那边同款");
  assert.doesNotMatch(libraryJs, /tagPickerCreateTag/,
    "右上角那个孤零零的「新建标签」按钮去掉了");
}

// ---- 六、单题那边什么也列不出来时，说清楚差哪一步 --------------------------
{
  // 浏览器里一次都没用过标签（本地没有「最近用过的」），又没输入名字。
  const inline = makeContext({ localStorage: { getItem: () => null, setItem: () => {} } });
  const panel = inline.context.buildInlineAddPanel({ id: "pub-1", tag_assignments: [], tag_revision: 1 }).panel;
  const hint = walk(panel, el => String(el.className || "").split(" ").includes("library-inline-add-hint"))[0];
  assert.match(textOf(hint), /这里空着|输入一个标签名/,
    "空着也要说清差哪一步、去哪做，不能光给一个空盒子");
  assert.equal(walk(panel, el => String(el.className || "").split(" ").includes("library-inline-add-item")).length, 0,
    "真的没有可列的");
}

// ---- 六之二、输入的名字目录里没有 → 最后一条就是「新建并贴上」 -------------
{
  const inline = makeContext();
  setInVm(inline.context, 'inlineAdd.query = "圆与圆";');
  const panel = inline.context.buildInlineAddPanel({ id: "pub-1", tag_assignments: [], tag_revision: 1 }).panel;
  const items = walk(panel, el => String(el.className || "").split(" ").includes("library-inline-add-item"));
  const last = items[items.length - 1];
  assert.match(textOf(last), /新建「圆与圆」并贴上/,
    "输入的名字目录里没有，最后一条就是「新建并贴上」—— 跟批量窗口同款");
}

// ---- 七、样式上，两个窗口的候选行用同一套记号 -------------------------------
assert.match(libraryCss, /\.tag-picker-row\.is-create \{[^}]*border-style: dashed/,
  "新建那一行在样式上跟别处不一样，看得出来是新建");
assert.match(libraryCss, /\.tag-picker-dot \{[^}]*border-radius: 50%/,
  "批量窗口的圆点还是圆的");
assert.match(libraryCss, /\.library-inline-add-dot \{[^}]*border-radius: 50%/,
  "单题那边的圆点也是圆的 —— 两处同一副样子");

console.log("Adding a tag: the same list, the same rows, the same words, the same recent tags: OK");
