"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"); const vm = require("node:vm"); const path = require("node:path");
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.style = {}; this.dataset = {}; this.value = ""; this.checked = false; this.disabled = false; this.hidden = false; this.isConnected = true; }
  append(...children) { this.children.push(...children); for (const value of children) if (value && typeof value === "object") value.parentNode = this; }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  async emit(name, event = {}) { await Promise.all((this.listeners[name] || []).map(fn => fn({ preventDefault() {}, stopPropagation() {}, ...event }))); }
  showModal() { this.open = true; }
  close() { this.open = false; void this.emit("close"); }
  focus() {}
  select() {}
  querySelectorAll(selector) { const tags = selector.split(",").map(value => value.trim().toUpperCase()); return descend(this).slice(1).filter(value => tags.includes(value.tagName)); }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; }
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
const requests = [], saved = [], notices = [], clipboardWrites = [], confirmations = [], timers = [];
let clock = Date.now(), confirmDecision = true;
class ControlledDate extends Date { static now() { return clock; } }
let jobResult = null, failSave = false, revision = null, saveGate = null, readGate = null, pollGate = null, paperGate = null, jobNumber = 0, figureFixture = null, aiFixture = null, aiStale = false, originFixture = null, jobMode = "done", apiConfigured = true, settingsGate = null;
const paintFrames = [];
const handoffText = '仅处理本批任务：job_id=job-wait, publication_id=pub-1, kind=answer, solution_scope=true。使用后端给出的真实本机CLI领取题面并逐题回写结果，不开启全局功能。检查初稿并保存后才用于出卷。';
const root = { LibrarySolutions: require("./library-solutions.js"), localStorage: { getItem: () => null, setItem() {} }, setTimeout: (callback, delay) => { timers.push({ callback, delay }); return setTimeout(callback, delay); }, clearTimeout,
  requestAnimationFrame: callback => { paintFrames.push(callback); },
  navigator: { clipboard: { writeText: async value => clipboardWrites.push(value) } },
  fetch: async (url, opts = {}) => {
    requests.push({ url, opts }); let body;
    if (url === "/api/settings/library-ai") { assert(!opts.method, "The answer editor must never alter shared model/tag settings"); const configured = apiConfigured; if (settingsGate) await settingsGate; body = { mode: "assistant", configured, api_ready: configured, features: { knowledge_tags: false, ai_answer: false } }; }
    else if (opts.method === "POST" && url === "/api/library/jobs") { const payload = JSON.parse(opts.body); assert.deepEqual(payload.ids, ["pub-1"]); assert.equal(payload.solution_scope, true); assert.equal(payload.executor, "api"); assert(apiConfigured, "No job without a configured API"); jobResult = { id: `job-${++jobNumber}`, publication_id: "pub-1", executor: "api", status: jobMode === "done" ? "done" : jobMode === "api_running" ? "running" : "queued", ...(jobMode === "done" ? { result: { answer: "AI结果", analysis: "AI详细步骤" } } : {}) }; body = { queued: 1, executor: "api", jobs: [jobResult] }; }
    else if (opts.method === "POST" && url === "/api/library/jobs/cancel") { const payload = JSON.parse(opts.body); assert.deepEqual(payload.ids, [jobResult.id]); assert.equal(payload.solution_scope, true); jobResult = { ...jobResult, status: "failed", terminal_reason: "cancelled", cancelled: true }; body = { jobs: [jobResult], cancelled: 1 }; }
    else if (url.startsWith("/api/library/jobs?")) { body = { jobs: jobResult ? [jobResult] : [], assistant_handoff: { text: handoffText, publication_ids: ["pub-1"] } }; if (pollGate) await pollGate; }
    else if (opts.method === "POST" && url.endsWith("/solution")) {
      if (saveGate) await saveGate;
      if (failSave) return { ok: false, json: async () => ({ error: "另一窗口已修改，当前文字保留" }) };
      const payload = JSON.parse(opts.body); revision = { id: "saved-1", publication_id: "pub-1", answer: payload.answer, analysis: payload.analysis, figures: [] }; body = { solution: revision, base_revision: payload.sync_library ? revision.id : null };
    } else if (url.endsWith("/solution?revision=origin")) body = { solution: null, base_revision: "later-library-head", origin: { answer: original.content.answer, analysis: "" }, history: [] };
    else if (url.endsWith("/solution")) { body = { solution: figureFixture, base_revision: null, origin: originFixture || { answer: original.content.answer, analysis: "" }, ai_answer: aiFixture, ai_answer_stale: aiStale, history: [] }; if (readGate) await readGate; }
    else if (url.startsWith("/api/papers/")) { body = { paper: { pages: [{ page_idx: 0 }, { page_idx: 1 }] } }; if (paperGate) await paperGate; }
    else throw new Error("unexpected request " + url);
    return { ok: true, json: async () => body };
  } };
const QB = { renderQuestion: (target, value) => { target.replaceChildren(node("p", "", value.stem)); }, renderTypeset: (target, value) => target.replaceChildren(node("span", "", value)) };
const context = { window: root, document, AbortController, FormData, Date: ControlledDate, setTimeout, clearTimeout }; vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "library-answer-editor.js"), "utf8"), context);
const editor = root.LibraryAnswerEditor.create({ node, QB, notify: (...value) => notices.push(value), confirm: async value => { confirmations.push(value); return confirmDecision; }, onSaved: (...value) => saved.push(value) });
const settle = async () => { for (let index = 0; index < 10; index++) await Promise.resolve(); };
(async () => {
  await editor.open([original, other], { scope: "paper", focus: original.id, selected: [original.id] }); await settle();
  const workspace = descend(document.body).find(element => element.className === "answer-editor-workspace");
  assert.deepEqual(workspace.children.map(element => element.className), ["answer-editor-input-column", "answer-editor-preview-column"]);
  // 1.12.7: the left column is the full-height one — 原卷本题 on top, the fields below.
  const left = workspace.children[0];
  assert.deepEqual(left.children.map(element => element.className), ["answer-editor-source", "answer-editor-edit"]);
  assert(left.children[0].children.some(element => element.className === "answer-question"));
  assert(left.children[1].children.some(element => element.className === "answer-editor-fields"));
  assert(workspace.children[1].children.some(element => element.className === "paper answer-editor-preview"));
  // 1.12.7: fullscreen. The bar holds 返回 / 存题库 / 保存, the strip above the three
  // zones holds the question numbers, and nothing is pinned over the lower half.
  const bar = descend(document.body).find(element => element.className === "answer-editor-bar");
  const strip = descend(document.body).find(element => element.className === "answer-editor-strip");
  assert.deepEqual(byId("answerEditorDialog").children.map(element => element.className),
    ["answer-editor-bar", "answer-editor-strip", "answer-editor-workspace"]);
  assert(bar.children.includes(byId("answerEditorSave")) && descend(bar).includes(byId("answerEditorSync")),
    "保存答案解析 和 同时保存到题库 moved from the bottom bar into the top bar");
  assert(strip.children.some(element => element.className === "answer-editor-list"),
    "The question numbers are a strip under the top bar, not a left sidebar");
  assert.equal(descend(document.body).find(element => element.className === "answer-question").open, true,
    "原卷本题 starts open in the top-left zone");
  const answerCss = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");
  assert.doesNotMatch(answerCss, /\.answer-editor-footer|\.answer-editor-nav/,
    "1.12.7: the bottom sticky bar and the 230px sidebar are gone");
  assert.match(answerCss, /\.answer-editor-dialog \{[^}]*height: 100dvh;/);
  assert.match(answerCss, /\.answer-editor-workspace \{[^}]*grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\);/);
  // 1.12.7：进来就勾上正在看的那道题。以前还得再点一下眼前这道题才勾上，
  // 而人已经在这一题里改字了，忘了勾就等于这次白点。
  const checkedBoxes = () => descend(document.body).filter(element => String(element.className).startsWith("answer-list-row"))
    .map(row => row.children[0].checked);
  assert.deepEqual(checkedBoxes(), [true, false], "显式传了 selected 就照它说的办");
  await byText("返回").emit("click");
  await editor.open([other, original], { scope: "paper", focus: other.id }); await settle();
  assert.deepEqual(checkedBoxes(), [true, false], "没传 selected 时默认勾上会打开的那道题");
  await byText("返回").emit("click");
  await editor.open([original, other], { scope: "paper", focus: original.id, selected: [] }); await settle();
  assert.deepEqual(checkedBoxes(), [false, false], "显式传空数组（只保留在本机那一路）仍然一道都不勾");
  await editor.open([original, other], { scope: "paper", focus: original.id, selected: [original.id] }); await settle();
  // 「历史版本」下拉删了：它挂在「原卷」折叠面板底部，展开才看得见，
  // 可它切的是整个编辑区，和「原卷」不是一回事。
  assert.equal(descend(document.body).find(element => element["aria-label"] === "已保存的答案解析历史"), undefined);
  const originPanel = descend(document.body).find(element => element.className === "answer-origin");
  assert.equal(descend(originPanel).find(element => element.tagName === "SUMMARY").textContent, "原卷答案解析",
    "折叠面板标题不再写「与历史」——那里已经没有历史下拉了");
  assert.match(answerCss, /\.answer-editor-input-column \{ grid-column: 1;[^}]*grid-template-rows: minmax\(0, auto\) minmax\(0, 1fr\);/,
    "The left column stacks 原卷本题 over the editing area, so the two fill one full-height column");
  assert.match(answerCss, /\.answer-editor-preview-column \{ grid-column: 2;/);
  assert.equal(byId("answerEditorCopyTask"), undefined); assert(!document.body.textContent.includes("复制助手任务说明"));
  assert.equal(byId("answerEditorCheckAi").textContent, "刷新生成结果");
  assert(byId("answerEditorCheckAi").title.includes("不会重新生成"));
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
  let releaseImageSave; saveGate = new Promise(resolve => { releaseImageSave = resolve; });
  await byId("answerEditorDialog").emit("keydown", { key: "s", ctrlKey: true }); await settle();
  assert.equal(width.disabled, true); assert.equal(position.disabled, true); assert.equal(paragraph.disabled, true);
  assert.equal(descend(document.body).find(element => element["aria-label"] === "已保存的答案解析历史"), undefined,
    "「历史版本」下拉删了；保存进行中图片编辑照样锁住");
  releaseImageSave(); await settle(); saveGate = null;
  const noBlurSave = JSON.parse(requests.filter(value => value.opts.method === "POST" && value.url.endsWith("/solution")).at(-1).opts.body);
  assert.equal(noBlurSave.figures[0].display_width, 50); assert.equal(noBlurSave.figures[0].paragraph, 1);
  await byText("返回").emit("click");

  figureFixture = null; apiConfigured = false;
  jobResult = { id: "legacy-wait", publication_id: "pub-1", executor: "assistant", status: "queued" };
  const timerCount = timers.filter(value => value.callback.name === "pollJobs").length;
  await editor.open([original, other], { scope: "paper", selected: [original.id] }); await settle();
  const generationPosts = requests.filter(value => value.url === "/api/library/jobs" && value.opts.method === "POST").length;
  assert.equal(byId("answerEditorAi").hidden, true); assert.equal(byId("answerEditorApiSettings"), undefined);
  assert.match(byId("answerEditorApiNote").textContent, /设置 → 服务与密钥/);
  await byId("answerEditorAi").emit("click"); await settle();
  assert.equal(requests.filter(value => value.url === "/api/library/jobs" && value.opts.method === "POST").length, generationPosts, "Missing API cannot fall back to the current desktop assistant");
  assert.equal(timers.filter(value => value.callback.name === "pollJobs").length, timerCount, "Legacy assistant queues never start an endless background wait");
  assert(!document.body.textContent.includes("等待当前助手")); assert.equal(clipboardWrites.length, 0);
  const handEdit = byId("answerEditorResult"); handEdit.value = "取消任务也保留此文字"; await handEdit.emit("input");
  confirmDecision = false; await byText("返回").emit("click"); assert.equal(editor.isOpen(), true); assert.equal(handEdit.value, "取消任务也保留此文字", "Declining return preserves unsaved edits"); confirmDecision = true;
  await byId("answerEditorCancelAi").emit("click"); await settle();
  assert(document.body.textContent.includes("已取消，勾选可重试")); assert.equal(handEdit.value, "取消任务也保留此文字");
  await byText("返回").emit("click");
  apiConfigured = true;

  jobResult = { id: "failed-api", publication_id: "pub-1", executor: "api", status: "failed", error: "服务商拒绝请求" };
  await editor.open([original], { scope: "paper" }); await settle();
  assert.match(document.body.textContent, /服务商拒绝请求/);
  assert.match(document.body.textContent, /设置 → 服务与密钥/);
  assert.equal(byId("answerEditorApiSettings"), undefined, "API configuration stays in the shared settings window");
  assert.equal(byId("answerEditorResult").value, original.content.answer, "Generation failure preserves the existing answer");
  await byText("返回").emit("click");

  jobResult = null; aiFixture = { answer: "豆包既有结果", analysis: "豆包已给出的详细解析", fingerprint: "current-fingerprint" }; originFixture = { answer: "", analysis: "" };
  const aiSavedBefore = saved.length, aiPostsBefore = requests.filter(value => value.opts.method === "POST").length;
  await editor.open([{ ...original, ai_answer: aiFixture }]); await settle();
  assert.equal(byId("answerEditorResult").value, "豆包既有结果"); assert.equal(byId("answerEditorAnalysis").value, "豆包已给出的详细解析");
  assert(document.body.textContent.includes("现有 AI 参考初稿，尚未核对")); assert(!document.body.textContent.includes("尚未补齐答案解析"));
  assert.equal(saved.length, aiSavedBefore); assert.equal(requests.filter(value => value.opts.method === "POST").length, aiPostsBefore, "Prefilling an existing validated AI draft never starts generation or persists it");
  await byText("返回").emit("click");
  originFixture = { answer: "原卷确切答案", analysis: "" };
  await editor.open([original]); await settle();
  assert.equal(byId("answerEditorResult").value, "原卷确切答案"); assert.equal(byId("answerEditorAnalysis").value, "豆包已给出的详细解析", "Validated existing AI fills only the missing source field");
  await byText("返回").emit("click");
  figureFixture = { id: "manual", answer: "人工答案", analysis: "", figures: [] };
  await editor.open([original]); await settle();
  assert.equal(byId("answerEditorResult").value, "人工答案"); assert.equal(byId("answerEditorAnalysis").value, "", "An explicit manual solution never gets AI fields mixed in");
  await byText("返回").emit("click");
  figureFixture = null; originFixture = { answer: "", analysis: "" }; aiStale = true;
  await editor.open([{ ...original, ai_answer: aiFixture }]); await settle();
  assert.equal(byId("answerEditorResult").value, ""); assert.equal(byId("answerEditorAnalysis").value, "");
  assert(document.body.textContent.includes("题面已变化，未自动填入")); await byText("返回").emit("click");
  aiFixture = null; aiStale = false; originFixture = null;

  // Completed jobs remain available for comparison, but their task status must
  // not claim a successfully saved draft is still waiting to be saved.
  jobResult = null; jobMode = "done"; figureFixture = null; originFixture = { answer: "", analysis: "" };
  await editor.open([original], { selected: [original.id] }); await settle();
  await byId("answerEditorAi").emit("click"); await settle();
  const aiStatus = () => descend(document.body).find(element => element.className === "helper answer-ai-status").textContent;
  const taskState = () => descend(document.body).find(element => element.className === "answer-job-state done").textContent;
  assert(aiStatus().includes("检查并保存后才出卷"));
  failSave = true; await byId("answerEditorSave").emit("click");
  assert(aiStatus().includes("检查并保存后才出卷"), "A failed save must not label an AI draft as saved");
  failSave = false; await byId("answerEditorSave").emit("click");
  assert.equal(taskState(), "该初稿已保存"); assert(aiStatus().includes("初稿已保存，可用于出卷"));
  assert(!aiStatus().includes("检查并保存后才出卷"), "Successful save immediately updates the summary without waiting for another poll");
  await byId("answerEditorCheckAi").emit("click"); await settle();
  assert.equal(taskState(), "该初稿已保存");
  assert.equal(descend(document.body).find(element => element.className === "answer-ai-draft").hidden, true);

  jobResult = { ...jobResult, id: "later-different-draft", result: { answer: "另一份 AI 答案", analysis: "另一份 AI 解析" } };
  await byId("answerEditorCheckAi").emit("click"); await settle();
  assert.equal(byId("answerEditorResult").value, "AI结果"); assert.equal(byId("answerEditorAnalysis").value, "AI详细步骤", "Checking a new draft preserves the already saved version even for a previously requested publication");
  assert(taskState().includes("已保存解析保留")); assert(aiStatus().includes("另有 AI 初稿可对照"));
  const savedManualEdit = byId("answerEditorAnalysis"); savedManualEdit.value = "保存后又做了人工修改"; await savedManualEdit.emit("input");
  jobResult = { ...jobResult, id: "late-for-edited-saved", result: { answer: "AI结果", analysis: "AI详细步骤" } };
  await byId("answerEditorCheckAi").emit("click"); await settle();
  assert.equal(savedManualEdit.value, "保存后又做了人工修改", "A matching saved AI job never resets subsequent unsaved human edits");
  assert.equal(taskState(), "该初稿已保存"); await byText("返回").emit("click");

  figureFixture = { id: "existing-manual-with-image", answer: "  AI结果  ", analysis: "AI详细步骤\n", figures: [{ id: "manual-image", url: "/local/manual-image", display_width: 50, position: "after" }] };
  await editor.open([original], { selected: [original.id] }); await settle();
  assert.equal(taskState(), "该初稿已保存", "Saved text matches ignore edge whitespace and are independent of manually added figures");
  assert.equal(descend(document.body).find(element => element.className === "answer-ai-draft").hidden, true, "An already saved matching draft is not presented as an extra unsaved suggestion on reopen");
  await byId("answerEditorAi").emit("click"); await settle();
  assert.equal(byId("answerEditorResult").value, "  AI结果  "); assert.equal(byId("answerEditorAnalysis").value, "AI详细步骤\n");
  assert.equal(descend(document.body).filter(element => element.className === "answer-image-row").length, 1, "Checking or requesting AI cannot erase existing saved image placements");
  await byText("返回").emit("click");
  figureFixture = { id: "existing-human", answer: "人工保存答案", analysis: "人工保存解析", figures: [] };
  await editor.open([original], { selected: [original.id] }); await settle();
  await byId("answerEditorAi").emit("click"); await settle();
  assert.equal(byId("answerEditorResult").value, "人工保存答案"); assert.equal(byId("answerEditorAnalysis").value, "人工保存解析", "An explicit supplementary AI request supplies a comparison draft instead of silently replacing an existing human solution");
  assert(taskState().includes("已保存解析保留"));
  await byText("返回").emit("click"); figureFixture = null; originFixture = null;

  jobResult = null; jobMode = "api_running";
  await editor.open([original], { selected: [original.id] }); await settle();
  await byId("answerEditorAi").emit("click"); await settle();
  assert(document.body.textContent.includes("AI 正在解题")); assert.equal(byId("answerEditorCopyTask"), undefined);
  const apiPoll = timers.filter(value => value.callback.name === "pollJobs").at(-1);
  assert.equal(apiPoll.delay, 4000);
  const beforeWatchPause = timers.filter(value => value.callback.name === "pollJobs").length;
  clock += 10 * 60 * 1000 + 1; await apiPoll.callback(); await settle();
  assert.equal(timers.filter(value => value.callback.name === "pollJobs").length, beforeWatchPause);
  assert(document.body.textContent.includes("自动刷新已暂停"));
  const beforeRefreshPosts = requests.filter(value => value.opts.method === "POST").length;
  await byId("answerEditorCheckAi").emit("click"); await settle();
  assert.equal(requests.filter(value => value.opts.method === "POST").length, beforeRefreshPosts, "Refresh is read-only, not a request to generate or modify tags/API settings");
  assert(timers.filter(value => value.callback.name === "pollJobs").length > beforeWatchPause);
  jobResult = { ...jobResult, status: "failed", terminal_reason: "timed_out", timed_out: true, error: "执行超过时限" };
  await byId("answerEditorCheckAi").emit("click"); await settle();
  assert(document.body.textContent.includes("处理超时，勾选可重试")); assert(document.body.textContent.includes("执行超过时限"));
  await byText("返回").emit("click");

  jobResult = null; let releaseRead;
  readGate = new Promise(resolve => { releaseRead = resolve; }); figureFixture = { answer: "迟到旧内容", analysis: "旧解析" };
  const delayedOpen = editor.open([original]); await settle();
  const pendingRead = requests.filter(value => value.url.endsWith("/solution") && !value.opts.method).at(-1);
  await byText("返回").emit("click"); assert.equal(editor.isOpen(), false); assert.equal(pendingRead.opts.signal.aborted, true, "Return aborts a slow editor read immediately");
  readGate = null; figureFixture = { answer: "重新打开的内容", analysis: "新解析" };
  const firstPaperContext = { draft: "原组卷" }, laterPaperContext = { draft: "另一份组卷" };
  await editor.open([original], { scopeContext: firstPaperContext }); await settle(); releaseRead(); await delayedOpen;
  assert.equal(byId("answerEditorResult").value, "重新打开的内容", "A late aborted read cannot write into a newly opened editor");

  let releaseSave; saveGate = new Promise(resolve => { releaseSave = resolve; });
  const pendingResult = byId("answerEditorResult"); pendingResult.value = "关闭前已提交的保存"; await pendingResult.emit("input");
  const delayedSave = byId("answerEditorSave").emit("click"); await settle();
  const saveBeforeClose = requests.filter(value => value.opts.method === "POST" && value.url.endsWith("/solution")).at(-1), savedCount = saved.length;
  await byText("返回").emit("click"); assert.equal(editor.isOpen(), false);
  assert.equal(saveBeforeClose.opts.signal.aborted, false, "An already submitted save is still checked because abort cannot undo server persistence");
  assert(confirmations.at(-1).text.includes("返回不会撤销保存"));
  await editor.open([original], { scopeContext: laterPaperContext }); await settle();
  const reopenedResult = byId("answerEditorResult"); reopenedResult.value = "新一轮人工编辑"; await reopenedResult.emit("input");
  assert.equal(byId("answerEditorSave").disabled, true, "The same publication cannot start a duplicate save while the first result is unresolved");
  releaseSave(); await delayedSave; saveGate = null;
  assert.equal(saved.length, savedCount + 1, "A confirmed save still refreshes the outer paper after return");
  assert.equal(saved.at(-1)[2].scopeContext, firstPaperContext, "A late saved callback carries the original paper context by reference, never the reopened paper");
  assert.equal(reopenedResult.value, "新一轮人工编辑", "Save completion never overwrites fields of a reopened editor");
  assert.equal(byId("answerEditorSave").disabled, false); assert(notices.some(value => value[0].includes("返回后已确认")));
  await byText("返回").emit("click");

  let releasePoll; pollGate = new Promise(resolve => { releasePoll = resolve; });
  await editor.open([original]); await settle();
  const pendingPoll = requests.filter(value => value.url.startsWith("/api/library/jobs?")).at(-1);
  await byText("返回").emit("click"); assert(pendingPoll.opts.signal.aborted, "Return aborts in-flight status polling");
  pollGate = null; releasePoll(); await settle(); assert.equal(editor.isOpen(), false);

  figureFixture = null; jobResult = null;
  const cropItem = { ...original, document_id: "doc", content: { ...original.content, sources: [{ page_idx: 1 }] } };
  await editor.open([cropItem]); await settle(); await byText("从原卷裁图").emit("click");
  const cropImage = descend(document.body).find(element => element.alt === "原卷解析图来源页"), cropSurface = descend(document.body).find(element => element.className === "answer-crop-surface");
  cropImage.naturalWidth = 100; await cropImage.emit("load"); assert.equal(cropImage.dataset.ready, "true");
  await byText("收起").emit("click"); let releasePaper;
  paperGate = new Promise(resolve => { releasePaper = resolve; }); const reopenCrop = byText("从原卷裁图").emit("click"); await settle();
  assert.equal(cropImage.dataset.ready, "false", "Reopening immediately invalidates the previous page image before the slow page list returns");
  await cropSurface.emit("click", { button: 0, clientX: 10, clientY: 10 }); await cropSurface.emit("click", { button: 0, clientX: 50, clientY: 50 });
  assert.equal(byText("加入解析图").disabled, true, "A stale previous page cannot be cropped while the new page list is unresolved");
  releasePaper(); await reopenCrop; paperGate = null; await byText("返回").emit("click");
  const noticesBeforeImage = notices.length; await cropImage.emit("error"); assert.equal(notices.length, noticesBeforeImage, "A late image error cannot disturb the returned screen");
  assert(cropImage.src, "Returning closes the UI before synchronously releasing the large image");
  const paint = () => { const callbacks = paintFrames.splice(0); callbacks.forEach(callback => callback()); };
  paint(); paint();
  await editor.open([original]); await settle();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(byId("answerEditorResult").value, "原卷结果", "A deferred old-close cleanup cannot clear a quickly reopened editor");
  await byText("返回").emit("click");
  const preview = descend(document.body).find(element => element.className === "paper answer-editor-preview");
  assert(preview.children.length > 0, "Rendered math is retained until the return screen can paint");
  paint(); assert(preview.children.length > 0); paint();
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(preview.children.length, 0); assert.equal(cropImage.src, undefined, "Large hidden images release after both paint frames");

  // An old settings request must not re-enable API generation on a later
  // editor session whose configuration is missing.
  let releaseSettings; apiConfigured = true;
  settingsGate = new Promise(resolve => { releaseSettings = resolve; });
  const oldSettingsOpen = editor.open([original], { selected: [original.id] }); await settle();
  const oldSettingsRequest = requests.filter(value => value.url === "/api/settings/library-ai").at(-1);
  await byText("返回").emit("click"); assert.equal(oldSettingsRequest.opts.signal.aborted, true);
  settingsGate = null; apiConfigured = false;
  await editor.open([original], { selected: [original.id] }); await settle();
  releaseSettings(); await oldSettingsOpen; await settle();
  assert.equal(byId("answerEditorAi").hidden, true, "Late old configuration cannot enable generation in a new session");
  const missingPosts = requests.filter(value => value.opts.method === "POST").length;
  await byId("answerEditorAi").emit("click"); await settle();
  assert.equal(requests.filter(value => value.opts.method === "POST").length, missingPosts);
  apiConfigured = true;
  await byId("answerEditorCheckAi").emit("click"); await settle();
  assert.equal(byId("answerEditorAi").hidden, false, "Read-only refresh observes a newly configured API without changing shared assistant mode");
  apiConfigured = false;
  await byId("answerEditorAi").emit("click"); await settle();
  assert.equal(requests.filter(value => value.opts.method === "POST").length, missingPosts, "Generation rechecks a configuration that was cleared after the button appeared");
  await byText("返回").emit("click"); paint(); paint(); await new Promise(resolve => setTimeout(resolve, 5));
  console.log("Answer editor: explicit selected AI, direct unsaved drafts, failure preservation, source protection, scope and repeated-save guards: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
