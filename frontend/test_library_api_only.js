"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const begin = source.indexOf("  let queueBusy = false;");
const end = source.indexOf("  // 只在题库里有 AI", begin);
assert(begin >= 0 && end > begin, "Exercise the actual shared ordinary queue action, feedback included");

// 一个够用的假按钮和假状态行：只实现排队反馈真正用到的那几个成员。
// 真实按钮的文字是一个文本节点，换文案时不能碰 textContent（会把图标抹掉）。
function fakeButton(label, stateLine) {
  const text = { nodeType: 3, nodeValue: label };
  return { childNodes: [text], dataset: {}, attributes: {}, isConnected: true, text,
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; },
    closest() { return { querySelector: () => stateLine || null }; } };
}
function fakeStateLine() { return { hidden: true, dataset: {}, textContent: "" }; }

function setup({ checked = { mode: "api", api_ready: true }, posted = checked, getOkay = true, hold = false } = {}) {
  const calls = [], jobs = [], notices = [], loads = [], statuses = [];
  const stateLine = fakeStateLine();
  // 两个批量按钮：一个是正常可点的，一个本来就因为没配 API 而不可点。
  // 排队结束后那个本来不可点的必须仍然不可点 —— 恢复时顺手把它放开，
  // 等于把一个点了只会报错的按钮重新摆到用户面前。
  const batchButtons = [{ dataset: {}, disabled: false },
    { dataset: { disabledInSelectedView: "true" }, disabled: true }];
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
  document: { querySelectorAll: selector => (selector === "#aiBatchTools button" ? batchButtons : []) },
  // 抓住每秒回调而不是自己造一个，这样量到的就是代码里那条真的那句。
  window: { setInterval: (fn) => { tick = fn; ticking = true; return 0; },
            clearInterval: () => { tick = null; ticking = false; } } };
  vm.createContext(context); vm.runInContext(source.slice(begin, end), context);
  return { queue: context.queueJobs, calls, jobs, notices, loads, statuses, batchButtons, stateLine,
    button: fakeButton("补齐缺的参考答案 · 7 题", stateLine),
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
    const pending = h.queue("tags", { missing: true }, h.button, 368);

    // 请求还在飞，此刻就应该已经看得见变化了。
    assert.equal(h.button.text.nodeValue, "正在排队…", "the pressed button says so before the server replies");
    assert.equal(h.button.attributes["aria-busy"], "true", "the pressed button is announced as busy");
    assert.equal(h.batchButtons[0].disabled, true, "the other batch buttons are locked while one is queueing");
    assert.equal(h.batchButtons[1].disabled, true);
    assert.equal(h.stateLine.hidden, false, "the batch panel already says something");
    assert.match(h.stateLine.textContent, /正在登记 368 道题/);
    assert.match(h.statuses[0].text, /正在排队 368 道题/, "the status line reports the queueing immediately");
    // 秒数只能靠代码里那条每秒回调自己打出来，测试不自己造一个假的。
    await new Promise(resolve => setTimeout(resolve, 1050));
    h.tickNow();
    assert.match(h.stateLine.textContent, /已等 1 秒/, "a press that takes seconds must keep showing that time passed");

    h.release();
    await pending;
    assert.equal(h.button.text.nodeValue, "补齐缺的参考答案 · 7 题", "the button goes back to its own name");
    assert.equal(h.button.attributes["aria-busy"], undefined, "the busy marker is cleared");
    assert.equal(h.stateLine.hidden, true, "the panel stops claiming it is still queueing");
    assert.equal(h.batchButtons[0].disabled, false, "the buttons are unlocked again");
    assert.equal(h.batchButtons[1].disabled, true,
      "a button that was never clickable (no API configured) must not be re-enabled by the restore");
    assert.equal(h.isTicking(), false, "the seconds ticker is stopped, not left running");
    assert.match(h.statuses[h.statuses.length - 1].text, /已排队 1 道/, "the status line ends on the real result");
  }

  // 排不上队的时候也必须还原，不能把按钮永久卡在「正在排队…」。
  {
    const h = setup({ getOkay: false });
    await h.queue("tags", { ids: ["pub-1"] }, h.button, 5);
    assert.equal(h.button.text.nodeValue, "补齐缺的参考答案 · 7 题", "a failed queue still restores the button");
    assert.equal(h.batchButtons[0].disabled, false, "a failed queue still unlocks the batch buttons");
    assert.equal(h.batchButtons[1].disabled, true);
  }
  console.log("Ordinary library API generation: explicit API-only POST, feature scope preserved, settings races rejected without assistant fallback, and the pressed button reacts before the reply: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
