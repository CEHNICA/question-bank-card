"use strict";
// Run the shipped editor in a small DOM, including bubbled form events and
// intentionally late responses. No live server, original papers, or AI calls.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.value = "";
    this.disabled = false; this.isConnected = true; this.open = false; this.className = "";
    this.classList = { toggle: (name, enabled) => { const values = new Set(this.className.split(/\s+/).filter(Boolean)); if (enabled) values.add(name); else values.delete(name); this.className = [...values].join(" "); } };
  }
  append(...children) { this.children.push(...children); children.forEach(value => { if (typeof value === "object") value.parentNode = this; }); }
  replaceChildren(...children) { this.children.forEach(value => { if (typeof value === "object") value.parentNode = null; }); this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async emit(name, event = {}) {
    if (name === "click" && this.disabled) return;
    const value = { preventDefault() {}, stopPropagation() {}, target: this, ...event };
    await Promise.all((this.listeners[name] || []).map(fn => fn(value)));
    if (["input", "change", "submit"].includes(name) && this.parentNode) await this.parentNode.emit(name, value);
  }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() { this.focused = true; }
  querySelectorAll(selector) { const tags = selector.split(",").map(value => value.trim().toUpperCase()); return descend(this).slice(1).filter(value => tags.includes(value.tagName)); }
  get textContent() { return (this._text || "") + this.children.map(value => typeof value === "string" ? value : value.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
}
const descend = element => [element, ...element.children.filter(value => typeof value === "object").flatMap(descend)];
function fixture(id = "pub-old", sourceImage = false) {
  const figures = sourceImage ? [] : [
    { file: "figure-one.png", url: "/local/figure-one.png", slot: "stem", bbox: [0.1, 0.2, 0.4, 0.6], page_idx: 0 },
    { file: "figure-two.png", url: "/local/figure-two.png", slot: "A", bbox: [0.2, 0.3, 0.6, 0.7], page_idx: 1 }
  ];
  const content = { stem: "原题 $x^2$", options: { A: "甲", B: "乙" }, question_type: "single_choice", body_mode: sourceImage ? "source_image" : "text", figures,
    question_images: sourceImage ? [{ file: "original-crop.png", page_idx: 0, bbox: [0.1, 0.1, 0.9, 0.7] }] : [], answer: "原卷答案", analysis: "原卷解析" };
  return { ok: true, publication: { id, content_hash: "a".repeat(64), version: 2, content,
    solution: { id: "human-solution-old", answer: "人工答案", analysis: "人工解析", figures: [{ id: "manual-figure" }] } },
    revision: 11, content_hash: "a".repeat(64), body_mode: content.body_mode, stem: content.stem,
    options: content.options, question_type: content.question_type, figures, editable: true, reason: "" };
}
function harness() {
  const document = { body: new Element("body"), activeElement: new Element("button") };
  const node = (tag, className = "", text) => { const element = new Element(tag); element.className = className; if (text !== undefined) element.textContent = text; return element; };
  const requests = [], notices = [], saved = [], confirmations = [], renders = [], timers = new Map(), windowListeners = {};
  let timerId = 0, confirmDecision = true, getHandler = async () => ({ ok: true, body: fixture() }), postHandler = async (request) => {
    const original = fixture(request.url.split("/").at(-2));
    return { ok: true, body: { created: true, publication: { ...original.publication, id: "pub-new", version: 3, content: { ...original.publication.content, ...request.body } } } };
  };
  const root = { addEventListener: (name, fn) => { (windowListeners[name] ||= []).push(fn); } };
  const fetch = async (url, opts = {}) => {
    const request = { url, opts, body: opts.body ? JSON.parse(opts.body) : null }; requests.push(request);
    const reply = await (opts.method === "POST" ? postHandler(request) : getHandler(request));
    return { ok: reply.ok !== false, status: reply.status || 200, json: async () => clone(reply.body) };
  };
  const QB = { renderQuestion: (target, content, options) => { renders.push({ content: clone(content), options: clone(options) }); target.replaceChildren(node("p", "", content.stem)); } };
  const context = { window: root, document, fetch, AbortController,
    setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id; }, clearTimeout: id => timers.delete(id) };
  vm.createContext(context); vm.runInContext(fs.readFileSync(path.join(__dirname, "library-question-editor.js"), "utf8"), context);
  const editor = root.LibraryQuestionEditor.create({ node, QB, notify: (...value) => notices.push(value),
    confirm: async value => { confirmations.push(clone(value)); return confirmDecision; }, onSaved: (...value) => saved.push(value) });
  const all = () => descend(document.body);
  const button = text => all().find(value => value.tagName === "BUTTON" && value.textContent === text);
  const field = label => all().find(value => value.className === "library-question-editor-field" && value.children[0].textContent === label)?.children[1];
  const status = () => all().find(value => value.className === "library-question-editor-status").textContent;
  const form = () => all().find(value => value.tagName === "FORM");
  const figure = index => all().filter(value => value.className.split(/\s+/).includes("library-question-editor-figure"))[index];
  const edit = async (label, value, event = "input") => { const input = field(label); input.value = value; await input.emit(event); };
  const beforeUnload = () => { const event = { prevented: false, preventDefault() { this.prevented = true; } }; (windowListeners.beforeunload || []).forEach(fn => fn(event)); return event; };
  return { editor, requests, notices, saved, confirmations, renders, timers, document, button, field, form, figure, status, edit, beforeUnload,
    get: fn => { getHandler = fn; }, post: fn => { postHandler = fn; }, confirm: value => { confirmDecision = value; } };
}
test("opening reads the stored publication and preserves existing manual answers without writing", async () => {
  const h = harness(), data = fixture(), unchanged = clone(data); h.get(async () => ({ body: data }));
  await h.editor.open(data.publication);
  assert.equal(h.field("题干（公式可用 $…$）").value, data.stem);
  assert.equal(h.requests.length, 1); assert.equal(h.requests[0].opts.method, undefined);
  assert.equal(h.renders.at(-1).options.showAnswer, "none");
  assert.deepEqual(data, unchanged); assert.equal(h.saved.length, 0);
  await h.button("保存题目").emit("click");
  assert.equal(h.requests.length, 1, "Unchanged save closes without publishing a needless new version");
  assert.equal(h.editor.isOpen(), false);
});
test("text, options, type, figure placement and removal save one exact CAS payload and freeze controls", async () => {
  const h = harness(), data = fixture(), unchanged = clone(data), gate = deferred(); h.get(async () => ({ body: data }));
  h.post(async request => { await gate.promise; return { body: { created: true, publication: { ...data.publication, id: "pub-new", version: 3, content: { ...data.publication.content, ...request.body } } } }; });
  await h.editor.open(data.publication);
  await h.edit("题干（公式可用 $…$）", "修订 $\\frac{1}{2}$"); await h.edit("选项 A", "新甲"); await h.edit("选项 B", "  "); await h.edit("选项 E", "新戊");
  await h.edit("题型", "multiple_choice", "change");
  h.figure(0).children[1].value = "B"; await h.figure(0).children[1].emit("change");
  await h.figure(1).children[2].emit("click"); assert.equal(h.renders.at(-1).content.figures.length, 1);
  await h.figure(1).children[2].emit("click"); assert.equal(h.renders.at(-1).content.figures.length, 2, "Restore preserves the original asset");
  await h.figure(1).children[2].emit("click");
  const saving = h.button("保存题目").emit("click"); await settle();
  assert(h.form().querySelectorAll("input, textarea, select, button").every(value => value.disabled));
  await h.button("保存题目").emit("click");
  const posts = h.requests.filter(value => value.opts.method === "POST"); assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, { revision: 11, content_hash: "a".repeat(64), body_mode: "text", stem: "修订 $\\frac{1}{2}$", question_type: "multiple_choice", options: { A: "新甲", E: "新戊" }, figures: [{ file: "figure-one.png", slot: "B" }] });
  assert.equal(posts[0].opts.headers["X-QB-Request"], "1");
  gate.resolve(); await saving;
  assert.equal(h.saved.length, 1); assert.equal(h.saved[0][0].id, "pub-old"); assert.equal(h.saved[0][1].id, "pub-new");
  assert.equal(h.editor.isOpen(), false); assert.deepEqual(data, unchanged, "Old source, manual solution and provenance are immutable");
  assert(h.notices.some(value => value[0].includes("旧版已保留")));
});
for (const failure of ["validation", "stale409", "network"]) test(`${failure} failure retains every edit, rejects overwrite and allows retry`, async () => {
  const h = harness(); await h.editor.open({ id: "pub-old" });
  await h.edit("题干（公式可用 $…$）", "失败后保留的题干"); await h.edit("选项 A", "保留选项");
  h.figure(0).children[1].value = "C"; await h.figure(0).children[1].emit("change"); await h.figure(1).children[2].emit("click");
  h.post(async () => { if (failure === "network") throw Error("网络暂时不可用"); return { ok: false, status: failure === "stale409" ? 409 : 400, body: { error: failure === "stale409" ? "题目已在其他窗口修改，请重新载入；本次文字未覆盖原题" : "题干格式需要调整" } }; });
  await h.button("保存题目").emit("click");
  assert.equal(h.editor.isOpen(), true); assert.equal(h.field("题干（公式可用 $…$）").value, "失败后保留的题干");
  assert.equal(h.field("选项 A").value, "保留选项"); assert.equal(h.figure(0).children[1].value, "C"); assert.equal(h.figure(1).children[2].textContent, "恢复");
  assert.equal(h.saved.length, 0); assert.equal(h.button("保存题目").disabled, false);
  assert(h.status().includes("本次输入仍保留")); assert(h.form().querySelectorAll("input, textarea, select, button").every(value => !value.disabled));
  assert.equal(h.requests.at(-1).body.revision, 11, "A failed CAS never silently rebases onto the latest revision");
  h.post(async () => ({ body: { created: true, publication: { ...fixture().publication, id: "pub-retry" } } }));
  await h.button("保存题目").emit("click"); assert.equal(h.saved.at(-1)[1].id, "pub-retry");
});
test("noneditable and unsaved-close paths cannot publish or silently discard", async () => {
  const h = harness(); h.get(async () => ({ body: { ...fixture(), editable: false, reason: "来源题卡待处理" } }));
  await h.editor.open({ id: "pub-old" }); assert.equal(h.button("保存题目").disabled, true); assert.equal(h.status(), "来源题卡待处理");
  await h.button("保存题目").emit("click"); assert.equal(h.requests.length, 1); await h.button("返回").emit("click");
  h.get(async () => ({ body: fixture() })); await h.editor.open({ id: "pub-old" }); await h.edit("题干（公式可用 $…$）", "未保存");
  h.confirm(false); await h.button("返回").emit("click"); assert.equal(h.editor.isOpen(), true); assert.equal(h.field("题干（公式可用 $…$）").value, "未保存");
  assert.equal(h.beforeUnload().prevented, true); h.confirm(true); await h.button("返回").emit("click"); assert.equal(h.editor.isOpen(), false);
});
test("slow GET can return immediately and its late body cannot populate a reopened question", async () => {
  const h = harness(), gate = deferred(); h.get(async request => { const data = fixture(request.url.split("/").at(-2)); if (data.publication.id === "pub-old") await gate.promise; return { body: data }; });
  const opening = h.editor.open({ id: "pub-old" }); await settle(); const oldRequest = h.requests[0];
  await h.button("返回").emit("click"); assert.equal(h.editor.isOpen(), false); assert.equal(oldRequest.opts.signal.aborted, true);
  await h.editor.open({ id: "pub-other" }); await h.edit("题干（公式可用 $…$）", "第二题人工输入"); const before = h.renders.length;
  gate.resolve(); await opening;
  assert.equal(h.field("题干（公式可用 $…$）").value, "第二题人工输入"); assert.equal(h.renders.length, before); assert.equal(h.saved.length, 0);
});
test("return during POST preserves its outcome without touching a reopened editor or allowing duplicate same-question saves", async () => {
  const h = harness(), gate = deferred(); h.get(async request => ({ body: fixture(request.url.split("/").at(-2)) }));
  h.post(async request => { await gate.promise; return { body: { created: true, publication: { ...fixture(request.url.split("/").at(-2)).publication, id: "pub-result" } } }; });
  await h.editor.open({ id: "pub-old" }); await h.edit("题干（公式可用 $…$）", "保存中的题干");
  const saving = h.button("保存题目").emit("click"); await settle(); const post = h.requests.at(-1);
  h.confirm(false); await h.button("返回").emit("click"); assert.equal(h.editor.isOpen(), true);
  h.confirm(true); await h.button("返回").emit("click"); assert.equal(h.editor.isOpen(), false); assert.equal(post.opts.signal.aborted, false, "Return cannot revoke a server-side save");
  assert(h.confirmations.at(-1).text.includes("返回不会撤销保存")); assert.equal(h.beforeUnload().prevented, true);
  await h.editor.open({ id: "pub-old" }); await h.edit("题干（公式可用 $…$）", "重开输入"); await h.button("保存题目").emit("click");
  assert.equal(h.requests.filter(value => value.opts.method === "POST").length, 1); assert(h.notices.some(value => value[0].includes("保存结果待核对")));
  await h.button("返回").emit("click"); await h.editor.open({ id: "pub-other" }); await h.edit("题干（公式可用 $…$）", "别的题目保留");
  const before = h.renders.length; gate.resolve(); await saving;
  assert.equal(h.saved.length, 1); assert.equal(h.saved[0][0].id, "pub-old"); assert.equal(h.saved[0][1].id, "pub-result");
  assert.equal(h.editor.isOpen(), true); assert.equal(h.field("题干（公式可用 $…$）").value, "别的题目保留"); assert.equal(h.renders.length, before);
});
test("a late failed save cannot change another question's fields or release its active save lock", async () => {
  const h = harness(), oldGate = deferred(), newGate = deferred();
  h.get(async request => ({ body: fixture(request.url.split("/").at(-2)) }));
  h.post(async request => {
    if (request.url.includes("pub-old")) { await oldGate.promise; return { ok: false, status: 409, body: { error: "旧题版本冲突，本次输入未覆盖" } }; }
    await newGate.promise; return { body: { created: true, publication: { ...fixture("pub-other").publication, id: "pub-other-new" } } };
  });
  await h.editor.open({ id: "pub-old" }); await h.edit("题干（公式可用 $…$）", "旧题保存");
  const oldSave = h.button("保存题目").emit("click"); await settle(); await h.button("返回").emit("click");
  await h.editor.open({ id: "pub-other" }); await h.edit("题干（公式可用 $…$）", "新题保存");
  const newSave = h.button("保存题目").emit("click"); await settle();
  oldGate.resolve(); await oldSave;
  assert.equal(h.status(), "正在保存题目…"); assert.equal(h.field("题干（公式可用 $…$）").value, "新题保存");
  assert.equal(h.button("保存题目").disabled, true); assert(h.form().querySelectorAll("input, textarea, select, button").every(value => value.disabled));
  assert.equal(h.saved.length, 0); assert(h.notices.some(value => value[0].includes("旧题版本冲突")));
  newGate.resolve(); await newSave; assert.equal(h.saved.length, 1); assert.equal(h.saved[0][0].id, "pub-other");
});
test("a queued old read timeout only aborts its own controller after the editor reopens", async () => {
  const h = harness(), oldGate = deferred(), newGate = deferred();
  h.get(async request => { const id = request.url.split("/").at(-2); await (id === "pub-old" ? oldGate.promise : newGate.promise); return { body: fixture(id) }; });
  const oldOpen = h.editor.open({ id: "pub-old" }); await settle();
  const oldTimeout = [...h.timers.values()].find(value => value.delay === 15000);
  await h.button("返回").emit("click"); const newOpen = h.editor.open({ id: "pub-other" }); await settle();
  oldTimeout.callback(); assert.equal(h.requests[0].opts.signal.aborted, true); assert.equal(h.requests[1].opts.signal.aborted, false);
  oldGate.resolve(); await oldOpen; assert.equal(h.status(), "正在载入已保存题目…");
  newGate.resolve(); await newOpen; assert.equal(h.field("题干（公式可用 $…$）").value, "原题 $x^2$"); assert.equal(h.timers.size, 0);
});
test("source-image editing keeps the complete original crops and never submits duplicate or removed source assets", async () => {
  const h = harness(), data = fixture("pub-image", true), crops = clone(data.publication.content.question_images); h.get(async () => ({ body: data }));
  await h.editor.open(data.publication);
  assert.equal(h.field("题目正文").value, "source_image"); assert.equal(h.field("题干（公式可用 $…$）").disabled, true); assert.equal(h.field("选项 A").disabled, true);
  await h.edit("题型", "fill_blank", "change");
  assert.deepEqual(h.renders.at(-1).content.question_images, crops); assert.equal(h.renders.at(-1).content.body_mode, "source_image");
  await h.button("保存题目").emit("click");
  const sent = h.requests.at(-1).body; assert.equal(sent.body_mode, "source_image"); assert.deepEqual(sent.figures, []);
  assert.equal(Object.hasOwn(sent, "question_images"), false); assert.equal(Object.hasOwn(sent, "solution"), false);
  assert.deepEqual(data.publication.content.question_images, crops);
});
test("read and save deadlines are bounded and unconfirmed save preserves input without claiming cancellation", async () => {
  const h = harness();
  const untilAbort = request => new Promise((resolve, reject) => request.opts.signal.addEventListener("abort", () => { const error = Error("aborted"); error.name = "AbortError"; reject(error); }, { once: true }));
  h.get(untilAbort); const opening = h.editor.open({ id: "pub-old" }); await settle();
  const readTimer = [...h.timers.values()].find(value => value.delay === 15000); assert(readTimer); readTimer.callback(); await opening;
  assert(h.status().includes("读取超时")); assert.equal(h.button("保存题目").disabled, true);
  await h.button("返回").emit("click"); h.get(async () => ({ body: fixture() })); await h.editor.open({ id: "pub-old" });
  await h.edit("题干（公式可用 $…$）", "超时后仍保留"); h.post(untilAbort); const saving = h.button("保存题目").emit("click"); await settle();
  const saveTimer = [...h.timers.values()].find(value => value.delay === 30000); assert(saveTimer); saveTimer.callback(); await saving;
  assert(h.status().includes("保存结果尚未确认")); assert(h.status().includes("不会撤销已接收的保存")); assert.equal(h.field("题干（公式可用 $…$）").value, "超时后仍保留");
  assert.equal(h.button("保存题目").disabled, false); assert.equal(h.saved.length, 0); assert.equal(h.timers.size, 0);
});
test("the real library parent refreshes the new version while retaining old baskets and saved drafts", async () => {
  const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
  const start = source.indexOf("  let questionEditor = null;"), end = source.indexOf("  let answerRefreshPending", start);
  assert(start >= 0 && end > start, "Parent integration region exists");
  const before = fixture().publication, after = { ...clone(before), id: "pub-new", version: 3 };
  const state = { catalog: new Map([[before.id, before]]), basket: [before.id], draft: { id: "saved-draft", ids: [before.id], solutions: { [before.id]: "human-solution-old" } }, questionReturnId: before.id };
  const savedDraft = clone(state.draft), basket = clone(state.basket), refreshed = [], loaded = [], basketChecks = [], dialog = { open: true };
  let options, opens = 0;
  const context = { state, node() {}, QB: {}, toast() {}, confirmDialog() {}, $: () => dialog, openQuestion: value => refreshed.push(value), load: value => { loaded.push(value); return Promise.resolve(); },
    // 1.13.6：题面一改，篮里那道旧版本就作废了。这里只是**重新核一遍**，不是替老师
    // 换题——换了就得 Basket 里那一行如实说「已经有新版本」，再由他点「换新版本」。
    refreshBasket: value => { basketChecks.push(value); return Promise.resolve(); },
    window: { LibraryQuestionEditor: { create: value => { options = value; return { open: async () => { opens++; } }; } } } };
  vm.createContext(context); vm.runInContext(source.slice(start, end), context);
  await context.openQuestionEditor(before); await context.openQuestionEditor(before); assert.equal(opens, 2);
  options.onSaved(before, after);
  assert.equal(state.catalog.get(after.id), after); assert.equal(state.catalog.get(before.id), before);
  assert.equal(refreshed[0], after); assert.equal(loaded[0].quiet, true);
  assert.equal(basketChecks.length, 1, "改完题面要把试题篮重新核一遍");
  assert.equal(basketChecks[0].force, true, "篮里的编号没变，只能强制重核");
  assert.deepEqual(state.basket, basket); assert.deepEqual(state.draft, savedDraft, "Saved exam publications and their manual solution revisions are never silently replaced");
  dialog.open = false; options.onSaved(before, after); assert.equal(refreshed.length, 1, "A closed full-question viewer is not reopened by a late save");
});
