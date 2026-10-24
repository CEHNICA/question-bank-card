"use strict";
// 1.26.24：把一颗标签拖到另一颗上面时，除了「成为它的子标签」，还要能选「并列」
// ——两颗共享同一个上级。老师 10 月 24 日早上 7 点提的：
// 「这个不应该仅仅只有成为另一个标签儿的一个子标签儿，它应该同时还显示一个是否要并列，
//   就是说他们共享同一个副标签儿。」
//
// 这一版量的不是源码里有没有那几个字，是**真跑出来的两样东西**：
//   1. 拖完弹出来的那张卡片上，写着的话对不对、灰掉的理由看不看得见；
//   2. 点了「并列」之后，发给后台的到底是哪个动作、带了哪些上级。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");

// ---- 把要验的函数从源码里切出来 -------------------------------------------
function sliceFunction(source, name) {
  const start = source.search(new RegExp(`\\n {2}(async )?function ${name}\\(`));
  if (start === -1) return "";
  const rest = source.slice(start + 1);
  const next = rest.slice(1).search(/\n {2}(async )?function \w+\(/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}
const slice = (name) => sliceFunction(libraryJs, name);

const RELATION_PIECES = [
  "node", "tagDescendants", "tagParentList", "tagSameParents", "tagRelationOptions",
  "applyTagRelation", "nestTagUnder", "makeSiblingOf", "openTagRelationChoice",
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
    this.listeners = {}; this.id = ""; this.type = ""; this.title = "";
    this.classList = {
      add: (...names) => { this.className = [this.className, ...names].filter(Boolean).join(" "); },
      remove: (...names) => {
        this.className = String(this.className || "").split(" ").filter(n => n && !names.includes(n)).join(" ");
      },
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
  close() { this.open = false; (this.listeners.close || []).forEach(fn => fn()); }
  showModal() { this.open = true; }
  querySelector(selector) {
    const want = String(selector).replace(/^\./, "");
    return walk(this, el => String(el.className || "").split(" ").includes(want))[0] || null;
  }
  querySelectorAll(selector) {
    const want = String(selector).replace(/^\./, "");
    return walk(this, el => String(el.className || "").split(" ").includes(want));
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

// 六颗标签：p 是上级；a 挂在 p 下面；b 自己独立；d 挂在 a 下面；g 挂在 d 下面（更下一层）。
const TAGS = [
  { id: "p", name: "初中 · 几何", kind: "knowledge", color: "#477a9e", parent_ids: [], child_ids: [] },
  { id: "a", name: "全等三角形", kind: "knowledge", color: "", parent_ids: ["p"], child_ids: [] },
  { id: "b", name: "相似三角形", kind: "knowledge", color: "", parent_ids: [], child_ids: [] },
  { id: "d", name: "全等判定", kind: "knowledge", color: "", parent_ids: ["a"], child_ids: [] },
  { id: "g", name: "全等判定条件", kind: "knowledge", color: "", parent_ids: ["d"], child_ids: [] },
  { id: "m", name: "数形结合", kind: "method", color: "", parent_ids: [], child_ids: [] },
];

function makeContext(extra = {}) {
  const requests = [];
  const toasts = [];
  const body = new Element("body");
  const context = vm.createContext({
    node: (tag, cls = "", text = "") => new Element(tag, cls, text),
    document: { body, createElement: (tag) => new Element(tag) },
    tagCatalogue: { tags: TAGS.map(t => ({ ...t })), revision: 7 },
    tagRequest: async (path, options) => {
      requests.push({ path, body: JSON.parse(options.body) });
      return { catalogue: { tags: TAGS.map(t => ({ ...t })) } };
    },
    tagRequestId: () => "req-1",
    loadTagCatalogue: async () => ({ tags: TAGS.map(t => ({ ...t })) }),
    toast: (message, kind) => { toasts.push({ message, kind }); },
    renderInspector: () => {},
    directoryRender: () => {},
    inspectorRenderedKey: "x",
    state: { catalog: { get: () => ({ id: "pub-1" }) } },
    ...extra,
  });
  vm.runInContext(RELATION_PIECES, context);
  return { context, requests, toasts, body };
}

const flush = () => new Promise(resolve => setTimeout(resolve, 0));

(async function main() {
  // ---- 一、拖上去之后给的是两个选择，不是直接挂上去 --------------------------
  {
    const { context } = makeContext();
    const options = context.tagRelationOptions(context.tagCatalogue.tags[1], context.tagCatalogue.tags[2]);
    assert.equal(options.length, 2, "两颗标签摆在一起就两个排法：成为子标签、并列");
    assert.deepEqual(Array.from(options, o => o.key), ["child", "sibling"], "两个排法都有名字");
  }

  // ---- 二、两个按钮的名字都写清「对谁做」 ----------------------------------
  {
    const { context } = makeContext();
    const a = context.tagCatalogue.tags[1], b = context.tagCatalogue.tags[2];
    const options = context.tagRelationOptions(a, b);
    for (const option of options) {
      assert.ok(option.label.includes("全等三角形") && option.label.includes("相似三角形"),
        `「${option.label}」这句里两颗标签都点了名 —— 老师得知道对谁做`);
      assert.ok(option.hint.includes("全等三角形"), "说明里也写着被处理的那颗");
    }
    assert.match(options[0].label, /让「全等三角形」成为「相似三角形」的子标签/);
  }

  // ---- 三、并列：目标有上级就写「同一个上级」，没有就写「都留在最外层」 --------
  {
    const { context } = makeContext();
    const a = context.tagCatalogue.tags[1], b = context.tagCatalogue.tags[2];
    const underParent = context.tagRelationOptions(b, a).find(o => o.key === "sibling");
    assert.match(underParent.label, /并列（同一个上级）/, "目标挂在「初中 · 几何」下面 → 并列就是同一个上级");
    assert.match(underParent.hint, /「相似三角形」和「全等三角形」都挂在「初中 · 几何」下面，并排着/,
      "说明里说清两颗最后挂在哪儿");
    assert.match(underParent.hint, /全等三角形/);

    const atTop = context.tagRelationOptions(a, b).find(o => o.key === "sibling");
    assert.match(atTop.label, /并列（都留在最外层）/, "目标自己就是最外层的 → 并列就是都留在最外层");
    assert.match(atTop.hint, /都留在最外层，谁也不套着谁/);
    assert.match(atTop.hint, /「全等三角形」现在挂在「初中 · 几何」下面，选这个就改挂/,
      "会动到原来那个上级，得先说清楚");
  }

  // ---- 四、灰掉的选项必须在卡片上写清为什么灰（悬停不算） -------------------
  {
    const { context } = makeContext();
    const a = context.tagCatalogue.tags[1], d = context.tagCatalogue.tags[3];
    const options = context.tagRelationOptions(a, d);   // a 是 d 的上级
    const child = options.find(o => o.key === "child");
    const sibling = options.find(o => o.key === "sibling");
    assert.ok(child.disabled, "把上级拖到下级身上挂不下去");
    assert.match(child.disabledReason, /是「全等判定」的上级，挂过去会成环/);
    assert.ok(sibling.disabled, "上级和它的下级没法并列");
    assert.match(sibling.disabledReason, /是「全等判定」的上级，两颗没法并列/);

    // 反过来拖：下级拖到上级身上 —— 挂下去本来就是那个关系，不用再放一次。
    const flipped = context.tagRelationOptions(d, a);
    assert.ok(flipped.find(o => o.key === "child").disabled, "本来就是那个关系，不用再放一次");
    assert.match(flipped.find(o => o.key === "child").disabledReason, /已经是「全等三角形」的子标签，不用再放一次/);
    assert.ok(!flipped.find(o => o.key === "sibling").disabled, "下级挪上去跟上级平级是可以的");

    // 更深一层：孙子拖到爷爷身上 —— 挂回去也成环（这条最容易漏）。
    const deep = context.tagRelationOptions(context.tagCatalogue.tags[4], a);
    assert.ok(deep.find(o => o.key === "child").disabled, "把更下面的一层拖上去挂，也会成环");
    assert.match(deep.find(o => o.key === "child").disabledReason, /是「全等三角形」的子标签，挂回去会成环/);
    assert.ok(!deep.find(o => o.key === "sibling").disabled, "挪上去跟爷爷平级是可以的");
  }

  // ---- 五、已经是同一个上级 → 并列灰掉，别让老师白点 -----------------------
  {
    const { context } = makeContext();
    const twin = { ...context.tagCatalogue.tags[1] };  // 跟 a 挂同一个上级
    const options = context.tagRelationOptions(context.tagCatalogue.tags[1], twin);
    const sibling = options.find(o => o.key === "sibling");
    assert.ok(sibling.disabled, "本来就并列，不用再排一次");
    assert.match(sibling.disabledReason, /已经是同一个上级的了/);
  }

  // ---- 六、点「并列」→ 后台收到的是 set_parents，上级取目标那颗的 ------------
  {
    const { context, requests } = makeContext();
    const a = context.tagCatalogue.tags[1], b = context.tagCatalogue.tags[2];
    await context.makeSiblingOf({ id: "pub-1" }, b, a);
    assert.equal(requests.length, 1, "只发一次");
    assert.equal(requests[0].body.action, "set_parents", "并列走的是整体替换上级那一条");
    assert.equal(requests[0].body.tag_id, "b");
    assert.deepEqual(requests[0].body.parent_ids, ["p"], "上级取的是目标那颗的上级");
  }

  // ---- 七、并列到最外层：上级给空数组，不是「不传」 ------------------------
  {
    const { context, requests } = makeContext();
    const a = context.tagCatalogue.tags[1], b = context.tagCatalogue.tags[2];
    await context.makeSiblingOf({ id: "pub-1" }, a, b);
    assert.equal(requests[0].body.action, "set_parents");
    assert.deepEqual(requests[0].body.parent_ids, [], "没有上级就传空数组");
  }

  // ---- 八、点「成为子标签」→ 后台收到的是 add_parent ------------------------
  {
    const { context, requests } = makeContext();
    const a = context.tagCatalogue.tags[1], b = context.tagCatalogue.tags[2];
    await context.nestTagUnder({ id: "pub-1" }, a, b);
    assert.equal(requests[0].body.action, "add_parent", "成为子标签还是在原那条路上");
    assert.equal(requests[0].body.parent_id, "b");
    assert.equal(requests[0].body.tag_id, "a");
  }

  // ---- 九、弹出来的那张卡片：两张都在、话说得清、灰的理由看得见 ------------
  {
    const { context, body } = makeContext();
    const a = context.tagCatalogue.tags[1], d = context.tagCatalogue.tags[3];
    context.openTagRelationChoice({ id: "pub-1" }, a, d);
    const cards = walk(body, el => String(el.className || "").split(" ").includes("library-tag-choice-option"));
    assert.equal(cards.length, 2, "弹出来的是两张卡片");
    for (const card of cards) {
      assert.ok(textOf(card).includes("全等三角形") && textOf(card).includes("全等判定"),
        "每张卡片都点着两颗标签的名");
    }
    const blocked = walk(cards[0], el => String(el.className || "").split(" ").includes("library-tag-choice-blocked"));
    assert.equal(blocked.length, 1, "灰掉的那张，卡片上写着一行为什么灰");
    assert.match(textOf(blocked[0]), /挂过去会成环/, "这行字是能直接读到的，不藏在悬停里");
    assert.ok(cards[0].disabled, "灰的那张点不动");
  }

  // ---- 十、灰的那张不能被点；不选就什么都不改 ------------------------------
  {
    const { context, requests, body } = makeContext();
    const a = context.tagCatalogue.tags[1], d = context.tagCatalogue.tags[3];
    context.openTagRelationChoice({ id: "pub-1" }, a, d);
    const cards = walk(body, el => String(el.className || "").split(" ").includes("library-tag-choice-option"));
    cards[0].listeners.click.forEach(fn => fn());
    await flush();
    assert.equal(requests.length, 0, "灰的那张点了也不动目录");
  }

  // ---- 十一、拖放本身不再直接挂上去，先问「怎么排」 ------------------------
  {
    const requests = [];
    const opened = [];
    const nested = [];
    const context = vm.createContext({
      toast: () => {},
      tagRequest: async (path, options) => { requests.push({ path, body: JSON.parse(options.body) }); return {}; },
      openTagRelationChoice: (item, dragged, target) => { opened.push({ dragged, target }); },
      nestTagUnder: (item, dragged, target) => { nested.push({ dragged, target }); },
      tagCatalogue: { tags: TAGS.map(t => ({ ...t })) },
    });
    vm.runInContext(slice("attachChipDnD"), context);
    const strip = new Element("div", "library-tags");
    const chip = new Element("div", "library-tag-chip");
    chip.dataset.tagId = "b"; chip.dataset.tagKind = "knowledge";
    // attachChipDnD 自己按选择器找那颗可拖的标签，这里就给它一颗。
    strip.querySelectorAll = (selector) => (String(selector).includes("draggable") ? [chip] : []);
    context.attachChipDnD(strip, { id: "pub-1" });
    assert.ok(strip.listeners.drop, "标签条上有接住松手的那一下");
    strip.listeners.drop.forEach(fn => fn({
      target: { closest: () => chip },
      preventDefault: () => {},
      dataTransfer: { getData: () => JSON.stringify({ id: "a", kind: "knowledge" }) },
    }));
    assert.equal(opened.length, 1, "松手以后弹的是「怎么排」，不是直接挂上去");
    assert.equal(nested.length, 0, "没选之前不许先把标签挂上去");
    assert.equal(opened[0].dragged.id, "a");
    assert.equal(opened[0].target.id, "b");
    assert.equal(requests.length, 0, "还没选，不许先动目录");
  }

  // ---- 十二、样式上，灰的理由那行是看得见的一行 ----------------------------
  assert.match(libraryCss, /\.library-tag-choice-blocked \{[^}]*font-weight: 600/,
    "灰掉的理由要显眼，不是灰字小字");
  assert.match(libraryCss, /\.library-tag-choice-option:disabled \{[^}]*background/,
    "灰掉的那张看起来就是灰的");
  assert.match(libraryCss, /\.library-tag-choice-dialog \{[^}]*height: fit-content/,
    "弹窗按内容高低收着 —— 弹窗是上下贴边的，写 height:auto 就撑成一屏高的空盒子");

  console.log("Dragging one tag onto another: the same two choices, the same words, the same action: OK");
})().catch(error => { console.error(error); process.exit(1); });
