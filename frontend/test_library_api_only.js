"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const begin = source.indexOf("  async function queueJobs("), end = source.indexOf("  // 只在题库里有 AI", begin);
assert(begin >= 0 && end > begin, "Exercise the actual shared ordinary queue action");

function setup({ checked = { mode: "api", api_ready: true }, posted = checked, getOkay = true } = {}) {
  const calls = [], jobs = [], notices = [], loads = [];
  const context = { fetch: async (url, options = {}) => {
    calls.push({ url, ...options });
    if (url === "/api/settings/library-ai") return { ok: getOkay, json: async () => checked };
    assert.equal(url, "/api/library/jobs");
    const payload = JSON.parse(options.body);
    // The production backend is tested separately against a real isolated DB.
    // Here it observes the newer settings after this UI's earlier ready GET.
    if (payload.api_only === true && (posted.mode !== "api" || posted.api_ready !== true)) return {
      ok: false, json: async () => ({ error: "答题 API 配置已变化，本次未创建助手任务。" }) };
    jobs.push({ executor: posted.mode, ...payload });
    return { ok: true, json: async () => ({ queued: 1, skipped: 0, executor: posted.mode }) };
  }, toast: (...args) => notices.push(args), load: options => loads.push(options) };
  vm.createContext(context); vm.runInContext(source.slice(begin, end), context);
  return { queue: context.queueJobs, calls, jobs, notices, loads };
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
  console.log("Ordinary library API generation: explicit API-only POST, feature scope preserved and settings races rejected without assistant fallback: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
