"use strict";
// 第三十轮守护：刷新页面之后，攒了一半的组卷还能不能接着改。
//
// 实测（_audit30.py，Playwright 真点真刷新）：
//   刷新前 6 道题在篮子里，标题「恢复测试卷」、题目＋答案合卷、答案卷末集中、
//   两行两个、14 磅、留白 30 毫米、尽量整题同页。
//   刷新后 —— 题还在（localStorage 的 qb-basket），其余七项全回到默认，
//   「正在编辑哪份草稿」也没了；再点一次「保存草稿」会按默认值另开一份，
//   原来那份一动不动。老师的卷子被刷新一次就散了。
//
// 现在组卷上下文（标题 + 打印设置 + 答案选择 + 正在编辑的草稿编号）也存在本机，
// 页面初始化时恢复。下面这些用例把这条契约钉死：
//   1. 存进去要能原样读回来
//   2. 篮子空了就把现场删掉，别留一份不存在的卷子
//   3. 刷新后标题、设置、正在编辑哪份草稿，全都回到刷新前的样子
//   4. 存不下的数据不能把页面搞坏
//   5. 篮子空的时候不许凭空恢复出一份卷子
//   6. 调用点不能被顺手删掉（篮子一变就存、初始化就恢复）
//
// 支持 QB_LIB_JS 指向另一份 library.js —— 看门狗靠它喂「打掉修复的旧版」自证。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const libPath = process.env.QB_LIB_JS || path.join(__dirname, "library.js");
const source = fs.readFileSync(libPath, "utf8");

const slice = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to);
  assert.ok(start > -1 && end > start, `切片锚点没了：${from} → ${to}`);
  return source.slice(start, end);
};

const CONTEXT_KEY = "qb-paper-context";

// ── 沙箱 ────────────────────────────────────────────────────────────
function makeSandbox({ storage = {}, fetchImpl } = {}) {
  const store = new Map(Object.entries(storage));
  const localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => { store.set(key, String(value)); },
    removeItem: (key) => { store.delete(key); },
  };
  const controls = Object.fromEntries([
    "printDocument", "printAnswerLayout", "printFontSize", "printAnswerSpace", "printStudentInfo",
    "printPagination", "printOptionLayout", "printOrigin", "printAi", "printAnswers", "saveDraft",
    "saveDraftAs", "draftSaveStatus", "printButton", "exportPdf", "exportWord", "exportSplit",
    "managePrintAnswers", "printExportStatus",
  ].map((id) => [id, { value: "", checked: false, disabled: false, hidden: false, title: "", textContent: "" }]));
  controls.printDocument.value = "questions";
  controls.printAnswerLayout.value = "inline";
  controls.printFontSize.value = "12";
  controls.printAnswerSpace.value = "auto";
  controls.printStudentInfo.checked = true;
  controls.printPagination.value = "compact";
  controls.printOptionLayout.value = "auto";
  const ui = { printTitle: { value: "" }, printOrigin: { checked: false }, printAi: { checked: false }, printAnswers: { checked: false } };
  const state = { basket: [], draft: null, draftDirty: false, draftBaseline: null, features: { ai_answer: false } };
  const printState = { optionOverrides: {}, answerSpaceOverrides: {}, figureOverrides: {}, questionBreaks: [], solutions: {} };
  const toasts = [];
  const sandbox = {
    window: { localStorage },
    $: (id) => controls[id],
    ui, state, printState,
    solutions: require("./library-solutions.js"),
    toast: (message) => { toasts.push(String(message)); },
    fetch: fetchImpl || (async () => ({ ok: true, status: 200, json: async () => ({ draft: { id: "d1", revision: 5 } }) })),
    encodeURIComponent,
  };
  vm.createContext(sandbox);
  vm.runInContext(slice("  const PAPER_CONTEXT_KEY", "  function saveBasket("), sandbox);
  vm.runInContext(slice("  function normalizePrintOptions(", "  function printAnswerContent("), sandbox);
  vm.runInContext(slice("  function syncDraftButtons(", "  async function saveDraft("), sandbox);
  for (const name of ["loadPaperContext", "savePaperContext", "restorePaperContext", "currentPrintOptions", "applyPrintOptions", "markDraftDirty"]) {
    assert.equal(typeof sandbox[name], "function", `${name} 切进来了`);
  }
  return { sandbox, store, controls, ui, state, printState, toasts };
}

test("组卷上下文存得住、读得回，刷新后原样恢复", async () => {
  // ── 1. 存进去要能原样读回来 ──────────────────────────────────────
  {
    const { sandbox, store, controls, state } = makeSandbox();
    state.basket = ["a", "b"];
    // 先把「恢复中」这道锁走完 —— 页面初始化就是这么走的
    sandbox.restorePaperContext();
    controls.printDocument.value = "combined";
    controls.printAnswerLayout.value = "appendix";
    controls.printFontSize.value = "14";
    controls.printAnswerSpace.value = "30";
    controls.printPagination.value = "keep";
    controls.printOptionLayout.value = "two";
    sandbox.ui.printTitle.value = "恢复测试卷";
    state.draft = { id: "d-1", revision: 3, title: "恢复测试卷" };

    sandbox.savePaperContext();
    assert.ok(store.has(CONTEXT_KEY), "组卷上下文存进本机了");
    const loaded = sandbox.loadPaperContext();
    assert.equal(loaded.title, "恢复测试卷", "标题存得住");
    assert.equal(loaded.draftId, "d-1", "正在编辑哪份草稿也记下来了");
    assert.equal(loaded.revision, 3, "草稿版本号一起记，不然保存时会被后端当旧版拦下");
    assert.equal(loaded.options.document, "combined", "输出内容存得住");
    assert.equal(loaded.options.answer_layout, "appendix", "答案位置存得住");
    assert.equal(loaded.options.font_size, 14, "字号存得住");
    assert.equal(loaded.options.answer_space, 30, "留白存得住");
    assert.equal(loaded.options.pagination, "keep", "分页存得住");
    assert.equal(loaded.options.option_layout, "two", "选项排版存得住");
  }

  // ── 2. 篮子空了，现场就该删掉 ────────────────────────────────────
  {
    const { sandbox, store, state } = makeSandbox();
    state.basket = ["a"];
    sandbox.restorePaperContext();
    sandbox.savePaperContext();
    assert.ok(store.has(CONTEXT_KEY), "有题的时候现场留着");
    state.basket = [];
    sandbox.savePaperContext();
    assert.ok(!store.has(CONTEXT_KEY), "篮子空了就把现场删掉，别留一份不存在的卷子");
  }

  // ── 3. 刷新后原样回来 ────────────────────────────────────────────
  {
    const context = {
      draftId: "d-9",
      revision: 4,
      title: "恢复测试卷",
      options: {
        document: "combined", answer_layout: "appendix", font_size: 14, answer_space: 30,
        student_info: true, pagination: "keep", option_layout: "two", origin: false, ai_answers: false,
      },
      solutions: { a: "origin" },
    };
    const { sandbox, controls, ui, state } = makeSandbox({
      storage: { [CONTEXT_KEY]: JSON.stringify(context) },
    });
    state.basket = ["a", "b"];
    sandbox.restorePaperContext();

    assert.equal(ui.printTitle.value, "恢复测试卷", "刷新后标题回来了");
    assert.equal(controls.printDocument.value, "combined", "刷新后输出内容回来了");
    assert.equal(controls.printAnswerLayout.value, "appendix", "刷新后答案位置回来了");
    assert.equal(controls.printFontSize.value, "14", "刷新后字号回来了");
    assert.equal(controls.printAnswerSpace.value, "30", "刷新后留白回来了");
    assert.equal(controls.printPagination.value, "keep", "刷新后分页回来了");
    assert.equal(controls.printOptionLayout.value, "two", "刷新后选项排版回来了");
    assert.ok(state.draft && state.draft.id === "d-9", "刷新后还在编辑原来那份草稿，不是变成新建");
    assert.equal(state.draft.revision, 4, "草稿版本号也得回来");
    assert.match(controls.draftSaveStatus.textContent, /正在编辑/, "状态栏得说清在编辑哪一份");
    assert.equal(state.draftDirty, false, "刚恢复回来不该一进门就催老师保存");
    assert.equal(controls.saveDraftAs.hidden, false, "知道在编辑哪份，复制那颗才该出现");
  }

  // ── 3b. 别处改过这份草稿时，版本号要跟上，别让保存永远被拦 ──────
  {
    const context = {
      draftId: "d-9", revision: 2, title: "恢复测试卷",
      options: { document: "questions", answer_layout: "inline", font_size: 12 },
    };
    const { sandbox, state } = makeSandbox({
      storage: { [CONTEXT_KEY]: JSON.stringify(context) },
      fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ draft: { id: "d-9", revision: 7 } }) }),
    });
    state.basket = ["a"];
    sandbox.restorePaperContext();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(state.draft.revision, 7, "草稿在别处改过，版本号要跟上，否则一保存就被后端当成旧版拦下");
  }

  // ── 3c. 草稿已经不在了，就别再假装在编辑它 ───────────────────────
  {
    const context = {
      draftId: "gone", revision: 1, title: "恢复测试卷",
      options: { document: "questions", answer_layout: "inline", font_size: 12 },
    };
    const { sandbox, state, toasts } = makeSandbox({
      storage: { [CONTEXT_KEY]: JSON.stringify(context) },
      fetchImpl: async () => ({ ok: false, status: 404, json: async () => ({ error: "找不到这份组卷草稿" }) }),
    });
    state.basket = ["a"];
    sandbox.restorePaperContext();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(state.draft, null, "草稿没了就别再挂着它");
    assert.ok(toasts.some((text) => /草稿/.test(text)), "得跟老师说一声，不然他以为还在改那份");
  }

  // ── 4. 坏数据不能把页面搞坏 ──────────────────────────────────────
  {
    const { sandbox, state } = makeSandbox({ storage: { [CONTEXT_KEY]: "这不是 JSON" } });
    assert.equal(sandbox.loadPaperContext(), null, "存的读不出来就当没有");
    state.basket = ["a"];
    assert.doesNotThrow(() => sandbox.restorePaperContext(), "存的读不出来也不能炸");
    const broken = makeSandbox({ storage: { [CONTEXT_KEY]: JSON.stringify({ draftId: 123, revision: "x", options: 5 }) } });
    // 解析的判断必须赶在恢复之前：恢复走完会把现场重写成一份正常的，
    // 那时再看就看不出坏数据被挡下来没有。
    assert.equal(broken.sandbox.loadPaperContext().draftId, null, "不是字符串的草稿编号不算数");
    assert.equal(broken.sandbox.loadPaperContext().options, null, "不是对象的打印设置不算数");
    assert.equal(broken.sandbox.loadPaperContext().revision, null, "不是整数的版本号不算数");
    broken.state.basket = ["a"];
    assert.doesNotThrow(() => broken.sandbox.restorePaperContext(), "字段类型不对也不能炸");
  }

  // ── 5. 篮子空的时候不许凭空恢复出一份卷子 ────────────────────────
  {
    const context = { draftId: "d-9", revision: 1, title: "恢复测试卷", options: { document: "combined" } };
    const { sandbox, controls, ui, state } = makeSandbox({ storage: { [CONTEXT_KEY]: JSON.stringify(context) } });
    state.basket = [];
    sandbox.restorePaperContext();
    assert.equal(ui.printTitle.value, "", "篮子是空的，标题不该凭空出现");
    assert.equal(controls.printDocument.value, "questions", "篮子是空的，输出内容不该凭空出现");
    assert.equal(state.draft, null, "篮子是空的，不该挂着一份草稿");
  }
});

test("调用点不能被顺手删掉", () => {
  // ── 6. 调用点不能被顺手删掉 ──────────────────────────────────────
  assert.match(source, /function saveBasket\(\)\s*\{[\s\S]{0,600}?savePaperContext\(\)/,
    "篮子一变就要把现场存下来 —— 少了这一步，刷新后又是空的");
  assert.match(source, /load\(\);[\s\S]{0,300}?restorePaperContext\(\)/,
    "页面初始化要把上次的现场接回来 —— 少了这一步，功能等于没做");
  assert.match(source, /savePaperContext\(\);[\s\S]{0,200}?toast\("组卷草稿已保存"/,
    "存完草稿要更新现场里的草稿编号，否则下次保存会存到旧的那份去");
});
