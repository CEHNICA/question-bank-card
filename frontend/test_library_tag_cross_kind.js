"use strict";
// 1.26.27：标签不再按类型分家。
//
// 老师 2026-10-24 晚上 7 点说的原话：
// 「知识点标签儿和什么各种各样标签儿之间啊，他没法就好像给强行区分开来了。
//   那我觉得其实没必要，他们都是标签儿而已，你没必要给他区分开。
//   我今天啊我拿两个标签儿，我想给他合并啊，想给他并列来着，
//   结果他给我说什么一个是知识点标签儿，一个是解题方法。」
//
// 这一版量的是**真跑出来的行为**，不是源码里有没有那句话：
//   1. 把一颗知识点标签拖到一颗解题方法标签上 → 真的发出请求（以前弹一句「不能相互挂靠」）；
//   2. 让两颗不同类型的标签并列 → 真的发出 set_parents；
//   3. 合并时「保留哪一颗」的下拉里，别类的标签也在（以前根本不出现在选项里）；
//   4. 目录里跨类型的子标签画得出来（以前按类型过滤，那颗会「消失」）。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const managerJs = fs.readFileSync(path.join(__dirname, "library-tag-manager.js"), "utf8");
const servicePy = fs.readFileSync(path.join(__dirname, "..", "backend", "core", "library_tagging.py"), "utf8");

function sliceFunction(source, name) {
  const start = source.search(new RegExp(`\\n {2}(async )?function ${name}\\(`));
  if (start === -1) return "";
  const rest = source.slice(start + 1);
  const next = rest.slice(1).search(/\n {2}(async )?function \w+\(/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}
const slice = (source, name) => {
  const code = sliceFunction(source, name);
  assert.ok(code, `能定位到「${name}」那段代码 —— 改名了要改这里`);
  return code;
};

// ---- 假浏览器 -------------------------------------------------------------
class Element {
  constructor(tag, cls = "", text = "") {
    this.tagName = tag; this.className = cls; this.textContent = text;
    this.children = []; this.hidden = false; this.dataset = {}; this.disabled = false;
    this.style = { values: {}, setProperty(name, value) { this.values[name] = value; } };
    this.listeners = {}; this.id = ""; this.type = ""; this.title = "";
    this.classList = {
      add: (...names) => { this.className = [this.className, ...names].filter(Boolean).join(" "); },
      remove: (...names) => { this.className = String(this.className || "").split(" ").filter((n) => n && !names.includes(n)).join(" "); },
      contains: (name) => String(this.className || "").split(" ").includes(name),
    };
  }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener() {}
  setAttribute() {}
  focus() {}
  remove() { this.removed = true; }
  close() { this.open = false; (this.listeners.close || []).forEach((fn) => fn()); }
  showModal() { this.open = true; }
  querySelector(selector) {
    const want = String(selector).replace(/^\./, "");
    return this.querySelectorAll("." + want)[0] || null;
  }
  querySelectorAll(selector) {
    const want = String(selector).replace(/^\./, "");
    const out = [];
    const walk = (element) => {
      element.children.forEach((child) => {
        if (String(child.className || "").split(" ").includes(want)) out.push(child);
        walk(child);
      });
    };
    walk(this);
    return out;
  }
}

// 一颗知识点、一颗解题方法、一个它们共同的上级。
const TAGS = [
  { id: "p", name: "初中 · 几何", kind: "knowledge", color: "", parent_ids: [], child_ids: [] },
  { id: "k", name: "分类讨论", kind: "knowledge", color: "", parent_ids: ["p"], child_ids: [] },
  { id: "m", name: "分类讨论法", kind: "method", color: "", parent_ids: [], child_ids: [] },
];

function makeContext(extra = {}) {
  const requests = [];
  const toasts = [];
  const body = new Element("body");
  const context = vm.createContext({
    node: (tag, cls = "", text = "") => new Element(tag, cls, text),
    document: { body, createElement: (tag) => new Element(tag) },
    tagCatalogue: { tags: TAGS.map((tag) => ({ ...tag })), revision: 9 },
    tagRequest: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      return { catalogue: { tags: TAGS.map((tag) => ({ ...tag })) } };
    },
    tagRequestId: () => "req-1",
    loadTagCatalogue: async () => ({ tags: TAGS.map((tag) => ({ ...tag })) }),
    toast: (message, kind) => { toasts.push({ message, kind }); },
    renderInspector: () => {},
    directoryRender: () => {},
    inspectorRenderedKey: "",
    state: { catalog: { get: () => ({ id: "pub-1" }) } },
    ...extra,
  });
  const pieces = [
    "tagDescendants", "tagParentList", "tagSameParents", "tagRelationOptions",
    "applyTagRelation", "nestTagUnder", "makeSiblingOf",
  ].map((name) => slice(libraryJs, name)).join("\n");
  vm.runInContext(pieces, context);
  return { context, requests, toasts };
}


(async function main() {
  // ---- 一、把知识点标签拖到解题方法标签上 → 真的挂上去 --------------------
  {
    const { context, requests, toasts } = makeContext();
    const [, knowledge, method] = context.tagCatalogue.tags;
    await context.nestTagUnder({ id: "pub-1" }, knowledge, method);
    assert.equal(requests.length, 1, "跨类型的挂靠要真的发出请求");
    assert.equal(requests[0].body.action, "add_parent");
    assert.equal(requests[0].body.tag_id, knowledge.id);
    assert.equal(requests[0].body.parent_id, method.id);
    assert.equal(toasts.filter((entry) => entry.kind === "error").length, 0,
      `不能再拦他：${toasts.map((entry) => entry.message).join(" / ")}`);
  }

  // ---- 二、让两颗不同类型的标签并列 → set_parents --------------------------
  {
    const { context, requests, toasts } = makeContext();
    const [, knowledge, method] = context.tagCatalogue.tags;
    await context.makeSiblingOf({ id: "pub-1" }, knowledge, method);
    assert.equal(requests.length, 1, "跨类型的并列要真的发出请求");
    assert.equal(requests[0].body.action, "set_parents");
    assert.deepEqual(requests[0].body.parent_ids, [], "目标自己是最外层的，并列就都留在最外层");
    assert.equal(toasts.filter((entry) => entry.kind === "error").length, 0,
      `不能再拦他：${toasts.map((entry) => entry.message).join(" / ")}`);
  }

  // ---- 三、那句「不能相互挂靠」不许再出现在源码里 --------------------------
  assert.doesNotMatch(libraryJs, /不能相互挂靠/, "拦他的那句话要去掉");
  assert.doesNotMatch(servicePy, /上下级须分别建立/, "后台那道类型关卡也要去掉");
  assert.doesNotMatch(servicePy, /只能合并同类型标签/, "合并的类型关卡也要去掉");

  // ---- 三之二、拖放那一下也不拦 -------------------------------------------
  {
    const opened = [];
    const scope = vm.createContext({
      toast: () => {},
      openTagRelationChoice: (item, dragged, target) => { opened.push({ dragged, target }); },
      tagCatalogue: { tags: TAGS.map((tag) => ({ ...tag })) },
    });
    vm.runInContext(slice(libraryJs, "attachChipDnD"), scope);
    const strip = new Element("div", "library-tags");
    const chip = new Element("div", "library-tag-chip");
    chip.dataset.tagId = "m"; chip.dataset.tagKind = "method";
    strip.querySelectorAll = (selector) => (String(selector).includes("draggable") ? [chip] : []);
    scope.attachChipDnD(strip, { id: "pub-1" });
    strip.listeners.drop.forEach((fn) => fn({
      target: { closest: () => chip },
      preventDefault: () => {},
      dataTransfer: { getData: () => JSON.stringify({ id: "k", kind: "knowledge" }) },
    }));
    assert.equal(opened.length, 1, "把知识点标签拖到解题方法标签上，要弹「怎么排」而不是拦下来");
    assert.equal(opened[0].dragged.id, "k");
    assert.equal(opened[0].target.id, "m");
  }

  // ---- 四、合并时「保留哪一颗」的下拉里，别类的标签也在 --------------------
  {
    const scope = vm.createContext({});
    const code = slice(managerJs, "mergeCandidates");
    vm.runInContext(`${code}\nthis.mergeCandidates = mergeCandidates;`, scope);
    const catalogue = TAGS.map((tag) => ({ ...tag, count: 1 }));
    const names = scope.mergeCandidates(catalogue, [catalogue[1]]).map((tag) => tag.name);
    assert.ok(names.includes("分类讨论法"), "保留哪一颗都能选 —— 别类的标签也要在列表里");
    assert.ok(names.includes("初中 · 几何"), "别的知识点也一样");
    // 自己不能既是源又是目标（只有一颗被选中时）。
    assert.ok(!names.includes("分类讨论"), "只选了它自己时，不能把它自己当目标");
  }

  // ---- 五、目录里跨类型的子标签画得出来（以前按类型过滤，那颗会消失） -------
  {
    const scope = vm.createContext({ directory: { tags: [
      { id: "p", name: "初中 · 几何", kind: "knowledge", parent_ids: [], child_ids: [] },
      { id: "k", name: "分类讨论", kind: "knowledge", parent_ids: ["p"], child_ids: [] },
      { id: "m", name: "分类讨论法", kind: "method", parent_ids: ["p"], child_ids: [] },
    ] } });
    const code = slice(libraryJs, "directoryChildren");
    vm.runInContext(`${code}\nthis.directoryChildren = directoryChildren;`, scope);
    const children = scope.directoryChildren(scope.directory.tags[0]);
    assert.equal(children.length, 2, "知识点下面挂着的那颗解题方法也要在目录里");
    assert.deepEqual(children.map((child) => child.name).sort(), ["分类讨论", "分类讨论法"]);
  }

  // ---- 六、标签管理页的树同理 ---------------------------------------------
  {
    assert.doesNotMatch(managerJs, /child\.kind === state\.hierarchyKind/,
      "树里不能按类型过滤子节点 —— 否则跨类型的那颗会「消失」");
    assert.match(managerJs, /tag-directory-tree-kind/,
      "跨类型的子标签要标出它是哪一类");
    assert.match(managerCss(), /\.tag-directory-tree-kind \{/, "这个标记要有样式");
  }

  console.log("Tags of different kinds: they can be merged, hung under each other, and lined up: OK");
})().catch((error) => { console.error(error); process.exit(1); });

function managerCss() {
  return fs.readFileSync(path.join(__dirname, "library-tag-manager.css"), "utf8");
}
