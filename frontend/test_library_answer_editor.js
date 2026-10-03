"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"); const vm = require("node:vm"); const path = require("node:path");
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.style = {}; this.dataset = {}; this.value = ""; this.checked = false; this.disabled = false; this.hidden = false; this.isConnected = true; }
  append(...children) { this.children.push(...children); for (const value of children) if (value && typeof value === "object") value.parentNode = this; }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async emit(name, event = {}) { await Promise.all((this.listeners[name] || []).map(fn => fn({ preventDefault() {}, stopPropagation() {}, ...event }))); }
  showModal() { this.open = true; }
  close() { this.open = false; void this.emit("close"); }
  focus() {}
  get textContent() { return (this._text || "") + this.children.map(child => typeof child === "string" ? child : child.textContent || "").join(""); }
  set textContent(value) { this._text = value; this.children = []; }
}
const document = { body: new Element("body"), activeElement: new Element("button"), createTextNode: value => value };
const node = (tag, className = "", text) => { const value = new Element(tag); value.className = className; if (text !== undefined) value.textContent = text; return value; };
const descend = element => [element, ...element.children.filter(child => typeof child === "object").flatMap(descend)];
const byId = id => descend(document.body).find(element => element.id === id);
const byText = text => descend(document.body).find(element => element.tagName === "BUTTON" && element.textContent === text);
const original = { id: "pub-1", number: 1, content: { stem: "问题一", answer: "原卷结果", analysis: "" } };
const other = { id: "pub-2", number: 2, content: { stem: "问题二", answer: "", analysis: "" } };
const requests = [], saved = [], notices = [];
let jobResult = null, failSave = false, revision = null, saveGate = null, jobNumber = 0, figureFixture = null;
const root = { LibrarySolutions: require("./library-solutions.js"), localStorage: { getItem: () => null, setItem() {} }, setTimeout, clearTimeout,
  fetch: async (url, opts = {}) => {
    requests.push({ url, opts }); let body;
    if (opts.method === "POST" && url === "/api/library/jobs") { const payload = JSON.parse(opts.body); assert.deepEqual(payload.ids, ["pub-1"]); assert.equal(payload.solution_scope, true); jobResult = { id: `job-${++jobNumber}`, publication_id: "pub-1", status: "done", result: { answer: "AI结果", analysis: "AI详细步骤" } }; body = { queued: 1, executor: "assistant" }; }
    else if (url.startsWith("/api/library/jobs?")) body = { jobs: jobResult ? [jobResult] : [] };
    else if (opts.method === "POST" && url.endsWith("/solution")) {
      if (saveGate) await saveGate;
      if (failSave) return { ok: false, json: async () => ({ error: "另一窗口已修改，当前文字保留" }) };
      const payload = JSON.parse(opts.body); revision = { id: "saved-1", publication_id: "pub-1", answer: payload.answer, analysis: payload.analysis, figures: [] }; body = { solution: revision, base_revision: payload.sync_library ? revision.id : null };
    } else if (url.endsWith("/solution?revision=origin")) body = { solution: null, base_revision: "later-library-head", origin: { answer: original.content.answer, analysis: "" }, history: [] };
    else if (url.endsWith("/solution")) body = { solution: figureFixture, base_revision: null, origin: { answer: original.content.answer, analysis: "" }, history: [] };
    else throw new Error("unexpected request " + url);
    return { ok: true, json: async () => body };
  } };
const QB = { renderQuestion: (target, value) => { target.replaceChildren(node("p", "", value.stem)); }, renderTypeset: (target, value) => target.replaceChildren(node("span", "", value)) };
const context = { window: root, document, AbortController, FormData, setTimeout, clearTimeout }; vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "library-answer-editor.js"), "utf8"), context);
const editor = root.LibraryAnswerEditor.create({ node, QB, notify: (...value) => notices.push(value), confirm: async () => true, onSaved: (...value) => saved.push(value) });
const settle = async () => { for (let index = 0; index < 10; index++) await Promise.resolve(); };
(async () => {
  await editor.open([original, other], { scope: "paper", focus: original.id, selected: [original.id] }); await settle();
  assert.equal(requests.filter(value => value.opts.method === "POST").length, 0, "Opening an editor never starts AI or saves anything");
  assert.equal(byId("answerEditorResult").value, "原卷结果");
  await byId("answerEditorAi").emit("click"); await settle();
  assert.equal(byId("answerEditorResult").value, "AI结果", "A requested AI draft appears directly in the editor");
  assert.equal(byId("answerEditorAnalysis").value, "AI详细步骤");
  assert.equal(saved.length, 0, "An AI draft is not an exportable saved solution");
  const result = byId("answerEditorResult"); result.value = "人工结果"; await result.emit("input");
  failSave = true; await byId("answerEditorSave").emit("click");
  assert.equal(result.value, "人工结果"); assert.equal(saved.length, 0); assert.equal(byId("answerEditorSave").disabled, false, "A failed save releases controls and preserves edits");
  failSave = false; await byId("answerEditorSave").emit("click");
  assert.equal(saved.length, 1); assert.equal(saved[0][1].answer, "人工结果"); assert.equal(saved[0][2].sync, false);
  assert.equal(original.content.answer, "原卷结果", "The immutable source is untouched");
  const lastSave = requests.filter(value => value.opts.method === "POST" && value.url.endsWith("/solution")).at(-1);
  assert.equal(JSON.parse(lastSave.opts.body).sync_library, false);
  result.value = "继续人工修改"; await result.emit("input");
  await byId("answerEditorAi").emit("click"); await settle();
  assert.equal(result.value, "继续人工修改", "A late AI draft cannot overwrite current human edits");
  assert.equal(descend(document.body).find(element => element.className === "answer-ai-draft").hidden, false);
  await byText("返回").emit("click"); assert.equal(editor.isOpen(), false);
  jobResult = null;
  await editor.open([original], { scope: "library" }); await settle();
  assert.equal(byId("answerEditorSync").checked, true); assert.equal(byId("answerEditorSync").disabled, true);
  let release; saveGate = new Promise(resolve => { release = resolve; });
  const first = byId("answerEditorSave").emit("click"); await settle(); const second = byId("answerEditorSave").emit("click"); await settle();
  const count = requests.filter(value => value.opts.method === "POST" && value.url.endsWith("/solution")).length;
  release(); await first; await second; saveGate = null;
  assert.equal(requests.filter(value => value.opts.method === "POST" && value.url.endsWith("/solution")).length, count, "Fast repeated saves cannot create duplicate revisions");
  assert.equal(saved.at(-1)[2].sync, true);
  await byText("返回").emit("click");
  await editor.open([{ ...original, solution_revision: "origin", solution: { id: "later-library-head", answer: "后来同步的答案", analysis: "新过程" } }], { scope: "paper" }); await settle();
  assert.equal(byId("answerEditorResult").value, "原卷结果", "A legacy paper starts editing the original answer, not the later synced answer");
  assert(requests.some(value => value.url.endsWith("/solution?revision=origin")));
  const newResult = byId("answerEditorResult"); newResult.value = "本次新编辑"; await newResult.emit("input");
  await byId("answerEditorSave").emit("click");
  const originSave = JSON.parse(requests.filter(value => value.opts.method === "POST" && value.url.endsWith("/solution")).at(-1).opts.body);
  assert.equal(originSave.base_revision, "later-library-head"); assert.equal(originSave.sync_library, false);
  assert.equal(saved.at(-1)[0].solution_revision, "saved-1", "Only saving the edit replaces the origin selection with an immutable revision");
  await byText("返回").emit("click");
  figureFixture = { id: "image-version", answer: "A", analysis: "第一段\n\n第二段", figures: [{ id: "asset", url: "/local/asset", display_width: 164.59199999999998, position: "after", paragraph: 0 }] };
  await editor.open([original], { scope: "paper" }); await settle();
  const width = descend(document.body).find(element => element.tagName === "INPUT" && element.min === "5");
  assert.equal(Number(width.value), 164.59);
  width.value = "50"; await width.emit("input"); assert.equal(width.value, "50", "Typing updates the model without rewriting the active control");
  const position = descend(document.body).find(element => element["aria-label"] === "解析图 1 插入位置");
  position.value = "paragraph"; await position.emit("change");
  const paragraph = descend(document.body).find(element => element["aria-label"] === "插入到第几段后");
  paragraph.value = "2"; await paragraph.emit("input");
  await byId("answerEditorDialog").emit("keydown", { key: "s", ctrlKey: true }); await settle();
  const noBlurSave = JSON.parse(requests.filter(value => value.opts.method === "POST" && value.url.endsWith("/solution")).at(-1).opts.body);
  assert.equal(noBlurSave.figures[0].display_width, 50); assert.equal(noBlurSave.figures[0].paragraph, 1);
  await byText("返回").emit("click");
  console.log("Answer editor: explicit selected AI, direct unsaved drafts, failure preservation, source protection, scope and repeated-save guards: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
