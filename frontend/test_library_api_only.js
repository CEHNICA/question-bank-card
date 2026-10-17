"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const begin = source.indexOf("  let queueBusy = false;");
const end = source.indexOf("  // 只在题库里有 AI", begin);
assert(begin >= 0 && end > begin, "Exercise the actual shared ordinary queue action, feedback included");

// 一个够用的假按钮和假状态行：只实现排队反馈真正用到的那几个成员。
// 真实按钮的文字是一个文本节点，换文案时不能碰 textContent（会把图标抹掉）。
function fakeButton(label, action) {
  const text = { nodeType: 3, nodeValue: label };
  return { childNodes: [text], dataset: { queueAction: action }, attributes: {}, disabled: false, text,
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; } };
}
function fakeStateLine() { return { hidden: true, dataset: {}, textContent: "" }; }

function setup({ checked = { mode: "api", api_ready: true }, posted = checked, getOkay = true, hold = false } = {}) {
  const calls = [], jobs = [], notices = [], loads = [], statuses = [];
  const stateLine = fakeStateLine();
  // 被点的那个按钮必须在下面这张列表里。真实页面上它就在 #aiBatchTools 内部，
  // 而界面每次重画后重新同步的也正是这张列表。把被点的按钮放在列表外面，
  // 等于把「排队期间界面会重画」这件事从测试里抹掉了 —— 真实代码里的那个 bug
  // 正是从这个缝里漏过去的：排队文案只写在点下去那一个、随时会被换掉的节点上。
  // 三个按钮各带一个渲染时就写死的身份，排队时只有身份对得上的那个改名。
  const button = fakeButton("补齐缺的参考答案 · 7 题", "answer");
  const other = fakeButton("补齐缺的知识点标签 · 1 题", "tags");
  const locked = fakeButton("只处理当前这批 · 4 题", "tags-current");
  locked.dataset.disabledInSelectedView = "true";
  locked.disabled = true;
  const batchButtons = [button, other, locked];
  // 把排队请求按在手里不放，才量得到「服务端还没回话时界面长什么样」。
  // 直接 await 一个立刻 resolve 的假响应，量到的只是结束后的样子。
  let release = null;
  const gate = hold ? new Promise(resolve => { release = resolve; }) : null;
  let tick = null, ticking = false;
  const context = { fetch: async (url, options = {}) => {
    calls.push({ url, ...options });
    if (url === "/api/settings/library-ai") return { ok: getOkay, json: async () => checked };
    assert.equal(url, "/api/library/jobs");
    if (gate) await gate;
    const payload = JSON.parse(options.body);
    // The production backend is tested separately against a real isolated DB.
    // Here it observes the newer settings after this UI's earlier ready GET.
    if (payload.api_only === true && (posted.mode !== "api" || posted.api_ready !== true)) return {
      ok: false, json: async () => ({ error: "答题 API 配置已变化，本次未创建助手任务。" }) };
    jobs.push({ executor: posted.mode, ...payload });
    return { ok: true, json: async () => ({ queued: 1, skipped: 0, executor: posted.mode }) };
  }, toast: (...args) => notices.push(args), load: options => loads.push(options),
  setStatus: (text, tone) => statuses.push({ text, tone }),
  confirmDialog: () => Promise.resolve(false),
  // 真正的 disable 判据就让它自己算：这里只提供它要用的 state 和 querySelectorAll。
  state: { view: "all" },
  document: {
    querySelectorAll: selector => (selector === "#aiBatchTools button" ? batchButtons : []),
    querySelector: selector => (selector === "#aiBatchTools .library-batch-state" ? stateLine : null)
  },
  // 抓住每秒回调而不是自己造一个，这样量到的就是代码里那条真的那句。
  window: { setInterval: (fn) => { tick = fn; ticking = true; return 0; },
            clearInterval: () => { tick = null; ticking = false; } } };
  vm.createContext(context); vm.runInContext(source.slice(begin, end), context);
  return { queue: context.queueJobs, calls, jobs, notices, loads, statuses, batchButtons, stateLine,
    button, other, locked,
    // 界面重画之后就是拿这张列表重新同步的，所以测试要能自己按一次那处同步。
    rerender: () => context.applyBatchButtonState(),
    release: () => { if (release) release(); },
    tickNow: () => { if (ticking && tick) tick(); }, isTicking: () => ticking };
}

(async () => {
  for (const [kind, target] of [["tags", { ids: ["pub-1"] }], ["answer", { ids: ["pub-1", "pub-2"] }], ["tags", { missing: true }], ["answer", { missing: true }]]) {
    const h = setup(); await h.queue(kind, target);
    assert.equal(h.calls.length, 2); assert.equal(h.calls[0].cache, "no-store");
    const post = h.calls[1]; assert.equal(post.method, "POST"); assert.equal(post.headers["X-QB-Request"], "1");
    assert.deepEqual(JSON.parse(post.body), { kind, ...target, api_only: true });
    assert.equal(h.jobs.length, 1); assert.equal(h.jobs[0].executor, "api");
    assert.equal(h.jobs[0].solution_scope, undefined, "Ordinary feature requests must not silently become solution-scope work");
    assert.equal(h.loads.length, 1); assert.match(h.notices[0][0], /已排队/); assert.equal(h.notices[0][1], "success");
  }
  const tampered = setup(); await tampered.queue("tags", { ids: ["pub-1"], api_only: false });
  assert.equal(JSON.parse(tampered.calls[1].body).api_only, true, "The shared API UI action cannot downgrade its API-only requirement");

  for (const configuration of [{ mode: "assistant", api_ready: true }, { mode: "api", api_ready: false }, { mode: "api" }]) {
    const h = setup({ checked: configuration }); await h.queue("tags", { ids: ["pub-1"] });
    assert.equal(h.calls.length, 1); assert.equal(h.jobs.length, 0); assert.equal(h.loads.length, 0);
    assert.equal(h.notices[0][1], "error", "No ordinary queue POST while initial API configuration is unavailable");
  }
  const failedGet = setup({ getOkay: false }); await failedGet.queue("answer", { ids: ["pub-1"] });
  assert.equal(failedGet.calls.length, 1); assert.equal(failedGet.jobs.length, 0);

  for (const posted of [{ mode: "assistant", api_ready: true }, { mode: "api", api_ready: false }]) {
    for (const kind of ["tags", "answer"]) {
      const h = setup({ posted }); await h.queue(kind, { ids: ["pub-1"] });
      assert.equal(h.calls.length, 2, "Changed settings cause a single refusal, without resubmit or assistant fallback");
      assert.equal(h.jobs.length, 0); assert.equal(h.loads.length, 0);
      assert.match(h.notices[0][0], /未创建助手任务/); assert.equal(h.notices[0][1], "error");
      assert.equal(JSON.parse(h.calls[1].body).api_only, true);
    }
  }

  // 「点了以为卡死」是这一版的重点，所以这里量的是行为，不是文案：
  // 请求还在飞的时候，按钮必须已经自己变了样、别的批量按钮必须已经锁上、
  // 面板上必须已经有一行字并且会打秒数。请求结束以后再一样样还原。
  {
    const h = setup({ hold: true });
    const pending = h.queue("answer", { missing: true }, "answer", 7);

    // 请求还在飞，此刻就应该已经看得见变化了。
    assert.equal(h.button.text.nodeValue, "正在排队…", "the pressed button says so before the server replies");
    assert.equal(h.button.attributes["aria-busy"], "true", "the pressed button is announced as busy");
    assert.equal(h.other.text.nodeValue, "补齐缺的知识点标签 · 1 题",
      "only the pressed button changes its wording — two buttons claiming to queue means the user thinks he queued twice");
    assert.equal(h.locked.text.nodeValue, "只处理当前这批 · 4 题", "the locked button keeps its own name too");
    assert.equal(h.other.disabled, true, "the other batch buttons are locked while one is queueing");
    assert.equal(h.locked.disabled, true);
    assert.equal(h.stateLine.hidden, false, "the batch panel already says something");
    assert.match(h.stateLine.textContent, /正在登记 7 道题/);
    assert.match(h.statuses[0].text, /正在排队 7 道题/, "the status line reports the queueing immediately");

    // 登记要好几秒，这期间界面会自己重画（题数回来了、定时刷新），按钮节点会被
    // 换成新的。「正在排队…」如果只写在点下去那一个节点上，重画之后按钮就变回
    // 原名、却仍然点不动 —— 正是「点了以为卡死」的老样子。
    // 这里换上一个全新的按钮节点（身份照旧，和真实重画一样），再走一次重画必经的那处同步。
    const rebuilt = fakeButton("补齐缺的参考答案 · 7 题", "answer");
    h.batchButtons[0] = rebuilt;
    h.rerender();
    assert.equal(rebuilt.text.nodeValue, "正在排队…",
      "a re-render mid-queue must not leave a silently disabled button behind");
    assert.equal(rebuilt.attributes["aria-busy"], "true", "the rebuilt button is still announced as busy");
    assert.equal(rebuilt.disabled, true, "the rebuilt button is still locked while queueing");
    assert.equal(h.stateLine.hidden, false, "the panel still says something after a re-render");

    // 秒数只能靠代码里那条每秒回调自己打出来，测试不自己造一个假的。
    await new Promise(resolve => setTimeout(resolve, 1050));
    h.tickNow();
    assert.match(h.stateLine.textContent, /已等 1 秒/, "a press that takes seconds must keep showing that time passed");

    h.release();
    await pending;
    // 屏幕上那个（中途被重画换过的）必须已经改回自己的名字。
    // 点下去那一个已经被丢掉、没人看得见，就不必再管它。
    assert.equal(rebuilt.text.nodeValue, "补齐缺的参考答案 · 7 题",
      "the button left on screen after a re-render goes back to its own name");
    assert.equal(rebuilt.attributes["aria-busy"], undefined, "the rebuilt button stops being announced as busy");
    assert.equal(h.stateLine.hidden, true, "the panel stops claiming it is still queueing");
    assert.equal(h.other.disabled, false, "the buttons are unlocked again");
    assert.equal(h.locked.disabled, true,
      "a button that was never clickable (no API configured) must not be re-enabled by the restore");
    assert.equal(h.isTicking(), false, "the seconds ticker is stopped, not left running");
    assert.match(h.statuses[h.statuses.length - 1].text, /已排队 1 道/, "the status line ends on the real result");
  }

  // 没有重画的那条路也要一样还原：按钮得回到自己的名字，不能永久停在「正在排队…」。
  {
    const h = setup({ hold: true });
    const pending = h.queue("answer", { missing: true }, "answer", 12);
    assert.equal(h.button.text.nodeValue, "正在排队…", "the pressed button says so before the server replies");
    h.release();
    await pending;
    assert.equal(h.button.text.nodeValue, "补齐缺的参考答案 · 7 题", "the button goes back to its own name");
    assert.equal(h.button.attributes["aria-busy"], undefined, "the busy marker is cleared");
    assert.equal(h.button.disabled, false, "the button is clickable again");
    assert.equal(h.locked.disabled, true);
  }

  // 排不上队的时候也必须还原，不能把按钮永久卡在「正在排队…」。
  {
    const h = setup({ getOkay: false });
    await h.queue("tags", { ids: ["pub-1"] }, "answer", 5);
    assert.equal(h.button.text.nodeValue, "补齐缺的参考答案 · 7 题", "a failed queue still restores the button");
    assert.equal(h.other.disabled, false, "a failed queue still unlocks the batch buttons");
    assert.equal(h.locked.disabled, true);
  }
  console.log("Ordinary library API generation: explicit API-only POST, feature scope preserved, settings races rejected without assistant fallback, and the pressed button reacts before the reply: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
