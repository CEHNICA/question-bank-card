"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.style = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this.isConnected = true; this.classes = new Set();
    this.classList = { add: value => this.classes.add(value), remove: value => this.classes.delete(value), contains: value => this.classes.has(value), toggle: (value, flag = !this.classes.has(value)) => flag ? this.classes.add(value) : this.classes.delete(value) }; }
  append(...children) { this.children.push(...children); children.forEach(child => { if (typeof child === "object") child.parentNode = this; }); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = String(value); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  emit(name, event = {}) { return Promise.all((this.listeners[name] || []).map(callback => callback({ preventDefault() {}, stopPropagation() {}, ...event }))); }
  set disabled(value) { this._disabled = Boolean(value); if (value && document.activeElement === this) document.activeElement = document.body; }
  get disabled() { return this._disabled; }
  showModal() { this.open = true; }
  close() { this.open = false; root.setTimeout(() => this.emit("close"), 0); }
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
const settle = async () => { for (let count = 0; count < 12; count++) await Promise.resolve(); };

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
  const answerHistory = byClass("question-viewer-answer-history");
  assert(answerHistory && !answerHistory.open, "Source and old AI draft remain in closed history after saving an answer");
  const primaryAnswer = byClass("question-viewer-answers").children.filter(child => child !== answerHistory).map(child => child.textContent).join("");
  assert(primaryAnswer.includes("人工保存版本")); assert(!primaryAnswer.includes("有效未核对参考")); assert(!primaryAnswer.includes("原卷推导"));
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

  // Navigation uses the current-result position rather than the original paper
  // number. Rapid clicks share the in-flight step, not a second page request.
  const navItems = [item, { ...item, id: "next-pub", number: 20, content: { stem: "完整下一题" } }, { ...item, id: "last-pub", number: 99, content: { stem: "完整末题" } }];
  const navCalls = []; let navGate = null, navFails = false;
  const nav = { index: 0, total: 3, load: async (index, { signal }) => {
    navCalls.push({ index, signal }); const waiting = navGate, failure = navFails;
    if (waiting) await waiting; if (failure) throw new Error("合成下一页读取失败"); return { item: navItems[index], index, total: 3 };
  } };
  response = { solution: null, ai_answer: null };
  await viewer.open(item, { returnFocus: caller, navigation: nav });
  assert.equal(byClass("question-viewer-position").textContent, "第 1/3 题"); assert(byId("questionViewerPrevious").disabled); assert(!byId("questionViewerNext").disabled);
  let releaseNav; navGate = new Promise(resolve => { releaseNav = resolve; });
  byId("questionViewerNext").focus(); const nextRead = byId("questionViewerNext").emit("click");
  assert.equal(document.activeElement, document.body, "Disabling a focused navigation button models the browser's focus loss");
  await byId("questionViewerNext").emit("click"); assert.equal(navCalls.length, 1); assert(byId("questionViewerPrevious").disabled && byId("questionViewerNext").disabled);
  navGate = null; releaseNav(); await nextRead; await settle();
  assert.equal(byId("questionViewerTitle").textContent, "第 20 题"); assert.equal(byClass("question-viewer-position").textContent, "第 2/3 题");
  assert.equal(document.activeElement, byId("questionViewerNext"), "Completion restores keyboard navigation after temporary disabling loses focus");

  let consumed = false;
  await byId("libraryQuestionViewer").emit("keydown", { key: "ArrowRight", target: byId("questionViewerNext"), preventDefault() { consumed = true; } }); await settle();
  assert(consumed); assert.equal(byClass("question-viewer-position").textContent, "第 3/3 题"); assert(byId("questionViewerNext").disabled);
  assert.equal(document.activeElement, byClass("question-viewer-viewport"), "At the last item the disabled Next button gives keyboard focus to the readable viewport");
  const input = { closest: selector => selector.startsWith("input") ? {} : null };
  const scrollField = { closest: selector => selector.startsWith(".qb-") ? { scrollWidth: 600, clientWidth: 200 } : null };
  const beforeIgnored = navCalls.length;
  for (const event of [{ isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { shiftKey: true }, { target: input }, { target: scrollField }]) {
    consumed = false; await byId("libraryQuestionViewer").emit("keydown", { key: "ArrowLeft", preventDefault() { consumed = true; }, ...event }); assert(!consumed);
  }
  assert.equal(navCalls.length, beforeIgnored, "IME, text inputs, selection modifiers and horizontally scrollable formulas keep their arrow keys");
  await byId("libraryQuestionViewer").emit("keydown", { key: "ArrowLeft" }); await settle(); assert.equal(byClass("question-viewer-position").textContent, "第 2/3 题");

  // Busy completion must not steal focus from a zoom/return interaction.
  let releaseFocus; navGate = new Promise(resolve => { releaseFocus = resolve; }); byId("questionViewerPrevious").focus();
  const focusRead = byId("questionViewerPrevious").emit("click"); byId("questionViewerZoomIn").focus();
  navGate = null; releaseFocus(); await focusRead; await settle(); assert.equal(document.activeElement, byId("questionViewerZoomIn"));
  assert.equal(byClass("question-viewer-position").textContent, "第 1/3 题");

  // An API answer check can stay in flight without blocking the next question.
  let releaseAnswer; gate = new Promise(resolve => { releaseAnswer = resolve; });
  await byId("questionViewerNext").emit("click"); assert.equal(byClass("question-viewer-position").textContent, "第 2/3 题");
  await byId("questionViewerNext").emit("click"); assert.equal(byClass("question-viewer-position").textContent, "第 3/3 题");
  gate = null; releaseAnswer(); await settle();

  // Failed and timed-out page reads retain the current question and allow retry.
  await viewer.open(item, { navigation: nav, returnFocus: caller }); navFails = true;
  await byId("questionViewerNext").emit("click"); assert.equal(byClass("question-viewer-position").textContent, "第 1/3 题");
  assert(byClass("question-viewer-navigation-status").textContent.includes("失败")); assert(!byId("questionViewerNext").disabled); navFails = false;
  let releaseDeadline; navGate = new Promise(resolve => { releaseDeadline = resolve; });
  const deadlineRead = byId("questionViewerNext").emit("click"); runTasks(15000); await deadlineRead;
  assert(navCalls.at(-1).signal.aborted); assert(byClass("question-viewer-navigation-status").textContent.includes("超时"));
  assert.equal(byClass("question-viewer-position").textContent, "第 1/3 题");
  navGate = null; await byId("questionViewerNext").emit("click"); releaseDeadline(); await settle();
  assert.equal(byClass("question-viewer-position").textContent, "第 2/3 题", "An ignored-abort old timeout response cannot advance a successful retry");

  let releaseClose; navGate = new Promise(resolve => { releaseClose = resolve; });
  const closingRead = byId("questionViewerNext").emit("click"); const closedSignal = navCalls.at(-1).signal;
  viewer.close(); assert(closedSignal.aborted); navGate = null; await viewer.open(item, { navigation: nav });
  releaseClose(); await closingRead; await settle(); assert.equal(byClass("question-viewer-position").textContent, "第 1/3 题");
  viewer.close();
  const closedCount = navCalls.length; await byId("libraryQuestionViewer").emit("keydown", { key: "ArrowRight" }); assert.equal(navCalls.length, closedCount);

  // A quiet list refresh may replace the original entry button. Resolve that
  // same card at close, not the last navigated question, without moving scroll.
  const detached = node("button"), replacement = node("button"); let resolverCalls = 0;
  await viewer.open(item, { navigation: nav, returnFocus: detached, returnFocusResolver: () => { resolverCalls++; return replacement; } });
  detached.isConnected = false; await byId("questionViewerNext").emit("click"); viewer.close();
  assert.equal(resolverCalls, 1); assert.equal(document.activeElement, replacement); assert.deepEqual(scrolls.at(-1), [15, 980]);

  // Native close is queued, while the application close must release its own
  // session immediately and only once. An old event cannot cancel a new read.
  runTasks(0);
  await viewer.open(item, { navigation: nav, returnFocus: caller });
  const beforeCloseScrolls = scrolls.length;
  viewer.close();
  assert.equal(scrolls.length, beforeCloseScrolls + 1); assert.equal(document.activeElement, caller);
  assert.equal(document.body.style.overflow, "");
  runTasks(0); assert.equal(scrolls.length, beforeCloseScrolls + 1, "The queued native event does not repeat return cleanup");

  await viewer.open(item, { navigation: nav, returnFocus: caller }); viewer.close();
  const freshCaller = node("button"); root.scrollX = 31; root.scrollY = 1217;
  let releaseFresh; gate = new Promise(resolve => { releaseFresh = resolve; });
  response = { solution: null, ai_answer: { answer: "新会话参考答案" }, ai_answer_stale: false };
  const freshRead = viewer.open(item, { navigation: nav, returnFocus: freshCaller });
  const freshSignal = requests.at(-1).options.signal, beforeOldEventScrolls = scrolls.length;
  runTasks(0);
  assert(viewer.isOpen()); assert(!freshSignal.aborted, "A late close must not abort the reopened reference check");
  assert.equal(scrolls.length, beforeOldEventScrolls); assert.equal(document.body.style.overflow, "hidden");
  gate = null; releaseFresh(); await freshRead;
  assert(byClass("question-viewer-answers").textContent.includes("新会话参考答案"));
  await byId("questionViewerNext").emit("click"); await settle();
  assert.equal(byClass("question-viewer-position").textContent, "第 2/3 题", "The reopened navigation survives the previous session's close event");
  viewer.close(); assert.equal(document.activeElement, freshCaller); assert.deepEqual(scrolls.at(-1), [31, 1217]);
  runTasks(0); assert.equal(document.activeElement, freshCaller);

  // External native close still restores focus. Reopening before that event
  // arrives first completes its previous return state, then owns a new one.
  response = { solution: null, ai_answer: null };
  await viewer.open(item, { navigation: nav, returnFocus: caller });
  const externalBefore = scrolls.length; byId("libraryQuestionViewer").close();
  assert.equal(scrolls.length, externalBefore); runTasks(0);
  assert.equal(scrolls.length, externalBefore + 1); assert.equal(document.activeElement, caller);
  await viewer.open(item, { navigation: nav, returnFocus: caller }); byId("libraryQuestionViewer").close();
  await viewer.open(item, { navigation: nav, returnFocus: freshCaller }); runTasks(0);
  assert(viewer.isOpen()); assert.equal(document.body.style.overflow, "hidden");
  await byId("questionViewerNext").emit("click"); await settle();
  assert.equal(byClass("question-viewer-position").textContent, "第 2/3 题");
  viewer.close(); assert.equal(document.activeElement, freshCaller); assert.equal(document.body.style.overflow, "");
  runTasks(0);

  // 1.12.7：专注开关是侧栏接缝上的小按钮，只切状态和图标，不再换位置、也没有文字。
  const seam = node("button"); seam.append(node("use"));
  const focus = root.LibraryQuestionViewer.mountFocus({ button: seam });
  assert.equal(focus.button, seam);
  focus.button.emit("click"); assert(document.body.classList.contains("library-focus-mode"));
  assert.equal(focus.button["aria-expanded"], "false", "Collapsed rail: the button offers to bring it back");
  assert.equal(focus.button.title, "展开筛选栏");
  focus.button.emit("click"); assert(!document.body.classList.contains("library-focus-mode"));
  assert.equal(focus.button["aria-expanded"], "true", "Rail back: the button now offers to collapse it");
  assert.equal(JSON.stringify(item), snapshot); assert(requests.every(value => !value.options.method));

  const css = fs.readFileSync(path.join(__dirname, "library.css"), "utf8"), library = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
  assert(css.includes(".question-viewer-dialog { width: calc(100vw - 16px)"));
  assert(css.includes("height: calc(100dvh - 16px)"));
  // 1.12.7：专注模式藏的是侧栏。篮已经搬进抽屉了，跟着一起藏的话，专注模式下
  // 点开抽屉只剩「试题篮 N」一个标题、下面一片空白。
  const shared = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
  assert(css.includes(".library-focus-mode .library-rail, .library-focus-mode .library-bulk"));
  assert(!css.includes(".library-focus-mode .basket-panel"), "专注模式不许再把篮藏掉");
  assert(shared.includes(".site-drawer .basket-list { max-height: none"), "抽屉里只留一根滚动条");
  assert(shared.includes(".topbar-basket-label, .topbar-preview-label"), "窄屏顶栏只留图标和数字");;
  assert(css.includes(".question-viewer-content.native-images .qb-question-image img"));
  assert(library.includes('"全屏看题"') && library.includes("openQuestionViewer(item, full)"));
  console.log("Question viewer: complete content, original image order/zoom, validated references, bounded navigation/keyboard/focus, Esc return, read abort and pure focus mode: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
