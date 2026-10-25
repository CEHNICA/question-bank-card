"use strict";
// 1.26.28：编辑标签的时候，别类的上级也得在 —— 不然一保存就被悄悄抹掉了。
//
// 这是 1.26.27 漏掉的一处：那一版把后台「上下级必须同一类」那道关卡拆了，
// 标签管理页的「设置父标签」也放开了 —— 老师把解题方法挂在知识点下面成了常事。
// 可编辑标签的那个「上级标签」下拉还按类型筛：别类的上级根本不列出来，
// 老师改个名字一保存，`parent_ids` 按下拉里选中的算，那条关系就没了，
// 而且界面上一点提示都没有 —— 标签还叫那个名字，看上去什么都没变。
//
// 这一版量的是**真跑出来的行为**：
//   1. 打开编辑窗口，下拉里那颗别类的上级在不在、在不在选中状态；
//   2. 真按「保存」，发出去的 parent_ids 里那颗上级还在不在；
//   3. 自己和自己的后代不能被当成上级（那会成环）。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");

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

// ---- 假浏览器：这次要能撑住 <select>，否则量不出下拉里到底有什么 -----------
class Element {
  constructor(tag, cls = "", text = "") {
    this.tagName = tag; this.className = cls; this.textContent = text;
    this.children = []; this.hidden = false; this.dataset = {}; this.disabled = false;
    this.style = { values: {}, setProperty(name, value) { this.values[name] = value; } };
    this.listeners = {}; this.id = ""; this.type = ""; this.title = ""; this.value = "";
    this.parentElement = null; this.selected = false;
    this.classList = {
      add: (...names) => { this.className = [this.className, ...names].filter(Boolean).join(" "); },
      remove: (name) => { this.className = String(this.className || "").split(" ").filter((n) => n && n !== name).join(" "); },
      contains: (name) => String(this.className || "").split(" ").includes(name),
    };
  }
  append(...kids) {
    kids.forEach((kid) => { if (kid && typeof kid === "object") kid.parentElement = this; });
    this.children.push(...kids);
  }
  replaceChildren(...kids) {
    this.children = [];
    this.append(...kids);
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener() {}
  setAttribute() {}
  focus() {}
  remove() { this.removed = true; }
  close() { this.open = false; (this.listeners.close || []).forEach((fn) => fn()); }
  showModal() { this.open = true; }
  add(option) { this.children.push(option); }
  get options() { return this.children; }
  get selectedOptions() { return this.children.filter((option) => option.selected); }
  querySelector(selector) {
    const want = String(selector).replace(/^\./, "");
    return this.querySelectorAll("." + want)[0] || null;
  }
  querySelectorAll(selector) {
    const want = String(selector).replace(/^\./, "");
    const out = [];
    const walk = (element) => {
      element.children.forEach((child) => {
        if (child && String(child.className || "").split(" ").includes(want)) out.push(child);
        walk(child);
      });
    };
    walk(this);
    return out;
  }
}

class Option {
  constructor(text, value) { this.textContent = text; this.value = value; this.selected = false; }
}

// 一棵跨类型的树：
//   p「初中 · 几何」（知识点）
//   m「分类讨论」（解题方法）—— 挂在 p 下面
//   k「分类讨论」（知识点）—— 挂在 m 下面  ← 编辑它，上级是别类的
//   c「角」（自定义）—— 挂在 k 下面        ← k 的后代，不能当上级
//   x「平行线」（知识点）
const TAGS = [
  { id: "p", name: "初中 · 几何", kind: "knowledge", active: true, parent_ids: [], child_ids: [] },
  { id: "m", name: "分类讨论", kind: "method", active: true, parent_ids: ["p"], child_ids: [] },
  { id: "k", name: "分类讨论", kind: "knowledge", active: true, parent_ids: ["m"], child_ids: [] },
  { id: "c", name: "角", kind: "custom", active: true, parent_ids: ["k"], child_ids: [] },
  { id: "x", name: "平行线", kind: "knowledge", active: true, parent_ids: [], child_ids: [] },
];

// 编辑窗口里的下拉嵌了好几层，按 id 找，别按层级猜。
function findById(root, id) {
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    if (current.id === id) return current;
    (current.children || []).forEach((kid) => { if (kid && typeof kid === "object") stack.push(kid); });
  }
  return null;
}

function makeContext() {
  const requests = [];
  const body = new Element("body");
  const catalogue = { tags: TAGS.map((tag) => ({ ...tag })), revision: 9 };
  const context = vm.createContext({
    node: (tag, cls = "", text = "") => new Element(tag, cls, text),
    Option,
    document: { body, createElement: (tag) => new Element(tag) },
    $: () => null,
    tagCatalogue: catalogue,
    tagCatalogueEditorDialog: null,
    tagManagerDialog: null,
    tagDialog: null,
    kindLabels: { knowledge: "知识点", method: "解题方法", custom: "自定义", legacy: "历史标签" },
    tagRequest: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      return { catalogue: { tags: TAGS.map((tag) => ({ ...tag })) } };
    },
    tagRequestId: () => "req-1",
    loadTagCatalogue: async () => catalogue,
    load: async () => {},
    loadTagOperationHistory: async () => {},
    toast: () => {},
  });
  const pieces = ["tagDescendants", "tagParentCandidates", "openTagCatalogueEditor"]
    .map((name) => slice(libraryJs, name)).join("\n");
  vm.runInContext(pieces, context);
  return { context, requests, body };
}

(async function main() {
  // ---- 一、下拉里那颗别类的上级在不在 ---------------------------------------
  {
    const { context, body } = makeContext();
    context.openTagCatalogueEditor(
      { id: "k", name: "分类讨论", kind: "knowledge", parent_ids: ["m"], related_method_ids: [], aliases: [] },
      false);
    const select = findById(body, "tagCatalogueParents");
    assert.ok(select, "编辑窗口里得能找到「上级标签」那个下拉");
    const values = select.options.map((option) => option.value);
    assert.ok(values.includes("m"), `别类的上级要在下拉里（现在只有 ${values.join("、")}）`);
    assert.ok(!values.includes("k"), "不能把自己当成自己的上级");
    assert.ok(!values.includes("c"), "自己的后代不能当成上级 —— 那会成环");
    assert.ok(values.includes("x"), "同类的上级照旧要能选");
  }

  // ---- 二、那颗上级是选中状态（不然一开窗口就把它丢了） --------------------
  {
    const { context, body } = makeContext();
    context.openTagCatalogueEditor(
      { id: "k", name: "分类讨论", kind: "knowledge", parent_ids: ["m"], related_method_ids: [], aliases: [] },
      false);
    const select = findById(body, "tagCatalogueParents");
    const chosen = select.selectedOptions.map((option) => option.value);
    assert.deepEqual(chosen, ["m"], "打开窗口时，那颗别类的上级得是选中的");
  }

  // ---- 三、真按保存 —— 发出去的上级还在不在 --------------------------------
  {
    const { context, requests, body } = makeContext();
    context.openTagCatalogueEditor(
      { id: "k", name: "分类讨论", kind: "knowledge", parent_ids: ["m"], related_method_ids: [], aliases: [] },
      false);
    const form = findById(body, "tagCatalogueEditorForm");
    const submit = form.listeners.submit[0];
    await submit({ preventDefault() {}, submitter: { value: "save" } });
    assert.equal(requests.length, 1, "保存要真的发一次请求");
    assert.equal(requests[0].body.action, "update_tag");
    assert.deepEqual(requests[0].body.parent_ids, ["m"],
      "那颗跨类型的上级不能在保存时被抹掉 —— 以前就是这样悄悄没的");
  }

  // ---- 四、同名的两颗标签，要标出类型，否则他分不清是哪一颗 ----------------
  {
    const { context, body } = makeContext();
    context.openTagCatalogueEditor(
      { id: "k", name: "分类讨论", kind: "knowledge", parent_ids: ["m"], related_method_ids: [], aliases: [] },
      false);
    const select = findById(body, "tagCatalogueParents");
    const option = select.options.find((item) => item.value === "m");
    assert.match(option.textContent, /解题方法/,
      `同名的标签要标出类型（现在写的是「${option.textContent}」）`);
  }

  // ---- 五、算法本身：后代认得跨类型，成环拦得住 --------------------------
  {
    const { context } = makeContext();
    const descendants = context.tagDescendants({ id: "p" }, TAGS);
    assert.ok(descendants.has("m"), "解题方法也是知识点的后代");
    assert.ok(descendants.has("k"), "知识点也是解题方法的后代（跨类型也要算）");
    assert.ok(descendants.has("c"), "再往下也算");
    const candidates = context.tagParentCandidates(TAGS, { excludeId: "p", keepIds: [] });
    const ids = candidates.map((row) => row.id);
    assert.ok(!ids.includes("p"), "自己不列");
    assert.ok(!ids.includes("m"), "直接后代不列");
    assert.ok(!ids.includes("k"), "隔一层的后代也不列");
  }

  console.log("Editing a tag keeps a parent of another kind: OK");
})().catch((error) => { console.error(error); process.exit(1); });
