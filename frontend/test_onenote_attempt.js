"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "eagle/index.html"), "utf8");
const funcs = ["openOneNotePage", "startOneNoteAttempt"].map(name => {
  const match = html.match(new RegExp(`async function ${name}\\([^]*?\\n    }`));
  assert.ok(match);
  return match[0];
}).join("\n");

function fixture({ warning = "", failure = false } = {}) {
  const calls = [], messages = [];
  const q = { id: "fake", onenote: { page_id: "old" } };
  const ctx = {
    questionsData: [q], API_BASE: "", ONE_NOTE_OPEN_STEPS: [], ONE_NOTE_CREATE_STEPS: [],
    oneNoteBusyBlocked: () => false, showOneNoteBusy: () => 1, hideOneNoteBusy: () => {},
    refreshOneNoteFilterAndCounts: () => {}, showToast: (...args) => messages.push(args),
    fetch: async (url, opts) => {
      calls.push({ url, opts });
      return { ok: !failure, json: async () => failure ? { error: "虚构失败" } : {
        ok: true, warning, onenote: { page_id: "old", attempts: [{ label: "今日重做", outline_id: "new" }] }
      } };
    }
  };
  vm.runInNewContext(funcs, ctx);
  return { ctx, calls, messages, q };
}

test("普通打开只走 open，加号走 attempt 并采用新区域记录", async () => {
  const f = fixture();
  await f.ctx.openOneNotePage("fake");
  await f.ctx.startOneNoteAttempt("fake");
  assert.equal(f.calls[0].url, "/api/library/fake/onenote/open?wait=1");
  assert.equal(f.calls[1].url, "/api/library/fake/onenote/attempt");
  assert.equal(f.q.onenote.attempts[0].outline_id, "new");
  assert.match(f.messages[1][0], /已新增并打开/);
});

test("定位失败仍保存新记录，但显示后端提醒而非成功定位", async () => {
  const warning = "已新增，请向右查看，无需再次点击＋";
  const f = fixture({ warning });
  await f.ctx.startOneNoteAttempt("fake");
  assert.equal(f.calls.length, 1);
  assert.equal(f.q.onenote.attempts[0].outline_id, "new");
  assert.deepEqual(f.messages[0], [warning, "info"]);
});

test("写入失败保留原关联并给出错误", async () => {
  const f = fixture({ failure: true });
  await f.ctx.startOneNoteAttempt("fake");
  assert.equal(f.q.onenote.attempts, undefined);
  assert.deepEqual(f.messages[0], ["虚构失败", "error"]);
});
