"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.style = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this.isConnected = true; this.classes = new Set();
    this.classList = { add: value => this.classes.add(value), remove: value => this.classes.delete(value), contains: value => this.classes.has(value), toggle: (value, flag = !this.classes.has(value)) => flag ? this.classes.add(value) : this.classes.delete(value) }; }
  append(...children) { this.children.push(...children); children.forEach(child => { if (typeof child === "object") child.parentNode = this; }); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = String(value); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  emit(name, event = {}) { for (const callback of this.listeners[name] || []) callback({ preventDefault() {}, stopPropagation() {}, ...event }); }
  showModal() { this.open = true; }
  close() { this.open = false; this.emit("close"); }
  focus(options) { document.activeElement = this; this.focusOptions = options; }
  get textContent() { return (this.text || "") + this.children.map(value => typeof value === "string" ? value : value.textContent || "").join(""); }
  set textContent(value) { this.text = String(value); this.children = []; }
}
const document = { body: new Element("body"), activeElement: new Element("button") };
const node = (tag, className = "", text) => { const value = new Element(tag); value.className = className; if (text !== undefined) value.textContent = text; return value; };
const descend = value => [value, ...value.children.filter(child => typeof child === "object").flatMap(descend)];
const byId = id => descend(document.body).find(value => value.id === id);
const byClass = className => descend(document.body).find(value => value.className?.split(" ").includes(className));
const frames = [], tasks = [], requests = [], renders = [], scrolls = [];
let gate = null, response = { solution: null, ai_answer: null, ai_answer_stale: false }, fail = false;
const root = { document, AbortController, scrollX: 15, scrollY: 980,
  setTimeout: (callback, delay) => { const value = { callback, delay, cancelled: false }; tasks.push(value); return value; },
  clearTimeout: value => { if (value) value.cancelled = true; },
  requestAnimationFrame: callback => frames.push(callback), scrollTo: (x, y) => scrolls.push([x, y]),
  fetch: async (url, options) => { assert(!options.method, "Viewing must never write to API or enqueue jobs"); requests.push({ url, options }); const current = response, failed = fail; if (gate) await gate; if (failed) throw new Error("offline read failed"); return { ok: true, json: async () => current }; }
};
const QB = { TYPE_NAMES: { single_choice: "单选题" }, renderTypeset: (target, text) => { target.textContent = text; },
  renderQuestion: (target, content, options) => { renders.push({ content, options }); target.replaceChildren(node("div", "formula", content.stem || ""));
    for (const [key, text] of Object.entries(content.options || {})) target.append(node("div", "option", `${key}. ${text}`));
    for (const image of [...content.figures || [], ...content.question_images || []]) { const element = node("img"); element.src = image.url; element.width = image.width; target.append(element); }
  }
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "library-question-viewer.js"), "utf8"), { window: root, globalThis: root });
const viewer = root.LibraryQuestionViewer.create({ node, QB, solutions: require("./library-solutions.js") });
const item = { id: "pub-1", number: 7, version: 3, source_filename: "离线数学卷.pdf", question_type: "single_choice", origin: "2026·离线",
  content: { stem: "完整长题干 $\\frac{1}{2}$\n(1) 第一问\n(2) 第二问", options: { A: "甲", B: "乙", C: "丙", D: "丁" },
    answer: "B", analysis: "原卷推导", figures: [{ slot: "stem", url: "/full/stem.png" }, ...["A", "B", "C", "D"].map(slot => ({ slot, url: `/full/${slot}.png` }))] },
  ai_answer: { answer: "未经指纹验证的旧 AI 答案", analysis: "不该直接显示" } };
const snapshot = JSON.stringify(item), caller = document.activeElement;
const runFrames = () => { const pending = frames.splice(0); pending.forEach(callback => callback()); };
const runTasks = delay => { tasks.filter(task => !task.cancelled && task.delay === delay).forEach(task => { task.cancelled = true; task.callback(); }); };

(async () => {
  await viewer.open(item, { returnFocus: caller });
  assert(viewer.isOpen()); assert.equal(byId("questionViewerTitle").textContent, "第 7 题");
  assert(byClass("question-viewer-source").textContent.includes("离线数学卷.pdf"));
  assert(byClass("question-viewer-detail").textContent.includes("2026·离线"));
  assert.equal(renders[0].content, item.content, "All question/options/figure content is passed intact to the shared renderer");
  assert.equal(renders[0].options.showAnswer, "none"); assert.equal(renders[0].options.imageLoading, "eager");
  const originalImages = descend(byClass("question-viewer-question")).filter(value => value.tagName === "IMG");
  assert.deepEqual(originalImages.map(value => value.src), item.content.figures.map(value => value.url));
  assert(byClass("question-viewer-answers").textContent.includes("原卷推导"));
  assert(!byClass("question-viewer-answers").textContent.includes("未经指纹验证"));
  assert.equal(descend(byClass("question-viewer-answers")).filter(value => value.tagName === "IMG").length, 0, "Question diagrams are not repeated as answer figures");
  assert.equal(requests[0].url, "/api/library/pub-1/solution");
  assert.equal(document.body.style.overflow, "hidden");
  byId("questionViewerZoomIn").emit("click"); assert.equal(byClass("question-viewer-percent").textContent, "125%");
  byClass("question-viewer-viewport").emit("wheel", { ctrlKey: true, deltaY: -80 }); assert.equal(byClass("question-viewer-percent").textContent, "135%");
  byId("questionViewerNative").emit("click"); assert(byClass("question-viewer-content").classList.contains("native-images"));
  assert.equal(byClass("question-viewer-percent").textContent, "100%");
  byId("questionViewerFit").emit("click"); assert(!byClass("question-viewer-content").classList.contains("native-images"));

  // Native dialog cancel is the actual Esc path and only closes this viewer.
  byId("libraryQuestionViewer").emit("cancel"); assert(!viewer.isOpen());
  assert.equal(document.activeElement, caller); assert.equal(caller.focusOptions.preventScroll, true);
  assert.deepEqual(scrolls.at(-1), [15, 980]); assert.equal(document.body.style.overflow, "");
  assert(byClass("question-viewer-question").children.length, "Large content is not synchronously destroyed during close");
  runFrames(); runFrames(); runTasks(0); assert.equal(byClass("question-viewer-question").children.length, 0);

  await viewer.open({ ...item, content: { ...item.content, answer: "", analysis: "" } });
  assert(!byClass("question-viewer-answers").textContent.includes("原卷答案与解析"), "A diagram-only question with no answer has no original-answer panel");
  assert.equal(descend(byClass("question-viewer-answers")).filter(value => value.tagName === "IMG").length, 0);
  viewer.close();
  response = { solution: { answer: "人工 C", analysis: "人工保存版本", figures: [] }, ai_answer: { answer: "AI D", analysis: "有效未核对参考" } };
  await viewer.open(item);
  const text = byClass("question-viewer-answers").textContent;
  assert(text.includes("原卷推导") && text.includes("人工保存版本") && text.includes("有效未核对参考") && text.includes("未核对"));
  assert.equal(JSON.stringify(item), snapshot, "Viewing never changes the publication, basket or a solution selection");

  const source = { ...item, id: "source-pub", content: { body_mode: "source_image", answer: "A", question_images: [
    { url: "/source/page3-piece2.png", page_idx: 2, width: 2100 }, { url: "/source/page1-piece1.png", page_idx: 0, width: 1900 }] } };
  response = { solution: null, ai_answer: { answer: "有效未核对参考" }, ai_answer_stale: true };
  await viewer.open(source);
  assert.equal(renders.at(-1).content, source.content);
  assert.deepEqual(descend(byClass("question-viewer-question")).filter(value => value.tagName === "IMG").map(value => value.src), source.content.question_images.map(value => value.url), "Cross-page pieces retain their saved order and original URLs");
  assert(byId("questionViewerStatus").textContent.includes("旧 AI"));
  assert(!byClass("question-viewer-answers").textContent.includes("有效未核对参考"));

  // A read that ignores abort still cannot modify a closed/reopened viewer.
  viewer.close();
  let release; gate = new Promise(resolve => { release = resolve; }); response = { solution: null, ai_answer: { answer: "旧请求迟到" } };
  const late = viewer.open(item); const signal = requests.at(-1).options.signal;
  viewer.close(); assert(signal.aborted);
  gate = null; response = { solution: null, ai_answer: null };
  await viewer.open(source); release(); await late; runFrames(); runFrames(); runTasks(0);
  assert(viewer.isOpen()); assert(byClass("question-viewer-question").children.length);
  assert(!byClass("question-viewer-answers").textContent.includes("旧请求迟到"));

  // The timeout bounds even transports that deliver after an ignored abort.
  let releaseTimeout; gate = new Promise(resolve => { releaseTimeout = resolve; }); response = { solution: null, ai_answer: { answer: "超时后迟到" } };
  const timeoutRead = viewer.open(item); runTasks(15000); gate = null; releaseTimeout(); await timeoutRead;
  assert(byId("questionViewerStatus").textContent.includes("超时")); assert(!byClass("question-viewer-answers").textContent.includes("超时后迟到"));
  fail = true; await viewer.open(item); fail = false;
  assert(byId("questionViewerStatus").textContent.includes("保留")); assert(byClass("question-viewer-question").textContent.includes("第二问"));
  viewer.close();

  const host = node("div"), focus = root.LibraryQuestionViewer.mountFocus({ node, host });
  focus.button.emit("click"); assert(document.body.classList.contains("library-focus-mode")); assert.equal(focus.button.textContent, "退出专注浏览");
  assert.equal(focus.button["aria-pressed"], "true");
  focus.button.emit("click"); assert(!document.body.classList.contains("library-focus-mode")); assert.equal(focus.button.textContent, "专注浏览");
  assert.equal(JSON.stringify(item), snapshot); assert(requests.every(value => !value.options.method));

  const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8"), library = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
  assert(css.includes(".question-viewer-dialog { width: calc(100vw - 16px)"));
  assert(css.includes("height: calc(100dvh - 16px)"));
  assert(css.includes(".library-focus-mode .library-toolbar, .library-focus-mode .basket-panel"));
  assert(css.includes(".question-viewer-content.native-images .qb-question-image img"));
  assert(library.includes('"全屏看题"') && library.includes("openQuestionViewer(item, full)"));
  console.log("Question viewer: complete content, original image order/zoom, validated references, Esc focus/scroll, read abort and pure focus mode: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
