"use strict";
// 1.26.30：题目来源（题源）——「更多」里能改。
//
// 老师 2026-10-25 说的原话：
// 「这几个新高考的题目来源它没有识别出来……就是这个题目来源的话，
//   最好是可以修改，放在更多里面，你想想该怎么设计。」
//
// 这一版量的是真跑出来的行为：
//   1. 打开弹窗，框里是这道题现在的出处；
//   2. 保存真的把出处发出去，窗口真的关上，出处真的落到题卡上；
//   3. 保存失败时窗口不关、话要说清楚；
//   4. 「改字」里不再带题源 —— 那件事只在「更多」里做，两处不能各改各的。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

function between(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.ok(from >= 0 && to > from, `能定位到「${start.trim()}」那段代码`);
  return source.slice(from, to);
}

const openCode = between("  function openOriginEditor(q) {", "  async function saveOrigin() {");
const saveCode = between("  async function saveOrigin() {", "  function applyQuestion(data) {");

function harness(options = {}) {
  const nodes = new Map();
  const requests = [];
  const notices = [];
  const applied = [];
  const $ = (id) => {
    if (!nodes.has(id)) {
      nodes.set(id, {
        id, value: "", dataset: {}, disabled: false, open: false,
        focused: false, removed: false,
        events: {},
        addEventListener(name, fn) { (this.events[name] ||= []).push(fn); },
        showModal() { this.open = true; }, focus() { this.focused = true; },
        close() { this.open = false; }, remove() { this.removed = true; },
      });
    }
    return nodes.get(id);
  };
  const context = vm.createContext({
    $, api: async (url, args) => {
      requests.push({ url, method: args.method, body: JSON.parse(JSON.stringify(args.body)) });
      if (options.failure) throw Error(options.failure);
      return options.response || { question: { id: 7, origin: options.saved ?? "2021新高考Ⅰ，7，5分，中", stem: options.savedStem ?? options.stem ?? "若过点(a,b)…" } };
    },
    questionById: (id) => ({ id, origin: options.current ?? "", stem: options.currentStem ?? options.stem ?? "若过点(a,b)…" }),
    applyQuestion: (data) => { applied.push(data); },
    toast: (message, kind) => { notices.push({ message, kind }); },
  });
  vm.runInContext(openCode + saveCode, context);
  return { context, $, requests, notices, applied, nodes };
}

(async function main() {
  // ---- 一、「更多」里有一项「修改题目来源」 --------------------------------
  {
    const menu = between('    const menu = el("div", "more-menu");', "    more.append(menu);");
    assert.match(menu, /修改题目来源/, "更多菜单里要有「修改题目来源」这一项");
    assert.match(menu, /openOriginEditor\(q\)/, "点它要打开改出处的窗口");
  }

  // ---- 二、弹窗的骨架在页面上，控件都有名字 --------------------------------
  {
    assert.match(html, /id="originDialog"/, "页面上要有这个弹窗");
    assert.match(html, /id="originInput"/, "要有输入框");
    assert.match(html, /id="originDialogSave"/, "要有保存按钮");
    assert.match(html, /id="originDialogTitle">修改题目来源/, "标题要写清楚改的是什么");
  }

  // ---- 三、打开时框里是这道题现在的出处 ------------------------------------
  {
    const h = harness({ current: "2021新高考Ⅰ，7，5分，中" });
    h.context.openOriginEditor({ id: 7, origin: "2021新高考Ⅰ，7，5分，中" });
    assert.equal(h.$("originInput").value, "2021新高考Ⅰ，7，5分，中", "框里要预填现在的出处");
    assert.equal(h.$("originDialog").open, true, "弹窗要打开");
    assert.equal(h.$("originInput").focused, true, "光标要落在框里");
  }

  // ---- 四、保存：真的发出去、窗口真的关上 ---------------------------------
  {
    const h = harness({ current: "", saved: "2021新高考Ⅰ，7，5分，中", stem: "若过点(a,b)…" });
    h.context.openOriginEditor({ id: 7, origin: "" });
    h.$("originInput").value = "2021新高考Ⅰ，7，5分，中";
    await h.context.saveOrigin();
    assert.deepEqual(h.requests, [{
      url: "/api/questions/7/origin", method: "POST", body: { origin: "2021新高考Ⅰ，7，5分，中" },
    }], "要发到改出处那个口子上");
    assert.equal(h.$("originDialog").open, false, "存好了要关窗口");
    assert.equal(h.applied.length, 1, "题卡要重画");
    assert.equal(h.notices.length, 1);
    assert.equal(h.notices[0].kind, "success");
    assert.match(h.notices[0].message, /题目来源已保存/);
  }

  // ---- 五、题干里那串括号被搬走了，要说一声 --------------------------------
  {
    const h = harness({
      current: "", saved: "2021新高考Ⅰ",
      currentStem: "（2021新高考Ⅰ，7，5分，中）若过点(a,b)…", savedStem: "若过点(a,b)…",
    });
    h.context.openOriginEditor({ id: 7, origin: "" });
    h.$("originInput").value = "2021新高考Ⅰ";
    await h.context.saveOrigin();
    assert.match(h.notices[0].message, /题干开头的出处已搬走/, "题干变了要说清楚");
  }

  // ---- 六、保存失败：窗口不关，话要说清楚 ---------------------------------
  {
    const h = harness({ failure: "题目已发生变化，请刷新后再操作" });
    h.context.openOriginEditor({ id: 7, origin: "" });
    h.$("originInput").value = "2021新高考Ⅰ";
    await h.context.saveOrigin();
    assert.equal(h.$("originDialog").open, true, "没存上就别关窗口");
    assert.equal(h.notices.length, 1);
    assert.equal(h.notices[0].kind, "error");
    assert.match(h.notices[0].message, /题目已发生变化/);
  }

  // ---- 七、「改字」里不再带题源 --------------------------------------------
  {
    const collect = between("    const collect = () => ({", "    editGuard.track(");
    assert.doesNotMatch(collect, /origin/,
      "改字保存时不能再带题源 —— 那件事只在「更多」里做，两处各改各的会打架");
    // 1.26.33：框选识读那一段改了（readTargetGuess 撤了），这里跟着往后挪一格。
    const editor = between("  function openEditorPanel(", "  // ---------------------------------------------------------------- 框选识读");
    assert.doesNotMatch(editor, /origin-input/,
      "改字面板里那个题源输入框撤掉了 —— 同一件事只留一个入口");
  }

  console.log("Editing the question source from the more menu: OK");
})().catch((error) => { console.error(error); process.exit(1); });
