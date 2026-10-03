"use strict";
const assert = require("node:assert/strict");
const Read = require("./app.js");

(async () => {
  const value = { question: { id: 7 } };
  assert.equal(await Read.boundedRequest(async () => value, { timeoutMs: 50 }), value);
  const serviceError = new Error("读题服务没有配置");
  await assert.rejects(Read.boundedRequest(async () => { throw serviceError; }), (error) => error === serviceError);

  let submitted = false;
  const cancelled = new AbortController(); cancelled.abort();
  await assert.rejects(Read.boundedRequest(async () => { submitted = true; }, { signal: cancelled.signal }), { name: "AbortError" });
  assert.equal(submitted, false, "an already cancelled request must never be sent");

  let timeoutSignal, lateResolve;
  const lateReply = new Promise((resolve) => { lateResolve = resolve; });
  await assert.rejects(Read.boundedRequest((signal) => { timeoutSignal = signal; return lateReply; }, { timeoutMs: 10 }), { name: "TimeoutError" });
  assert.equal(timeoutSignal.aborted, true, "timeout aborts the underlying HTTP request");
  await assert.rejects(Read.boundedRequest((signal) => new Promise((_, reject) => {
    signal.addEventListener("abort", () => reject(Object.assign(new Error("fetch aborted"), { name: "AbortError" })));
  }), { timeoutMs: 10 }), { name: "TimeoutError" });
  lateResolve({ question: { id: 7, stem: "迟到内容" } });
  const current = { question: { id: 7, stem: "原题" } };
  assert.equal(await Read.boundedRequest(async () => current, { timeoutMs: 50 }), current, "a late reply from another request cannot replace the next result");

  const controller = new AbortController(); let requestSignal;
  const pending = Read.boundedRequest((signal) => {
    requestSignal = signal; return new Promise(() => {});
  }, { signal: controller.signal, timeoutMs: 1000 });
  await Promise.resolve(); controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(requestSignal.aborted, true);

  const created = "2026-10-03T00:00:00Z", end = "2026-10-03T00:01:30Z";
  const job = { status: "queued", created_at: created, deadline_at: end };
  assert.equal(Read.deadline(job), Date.parse(end));
  assert.equal(Read.isExpired(job, 0, Date.parse(end) - 1), false);
  assert.equal(Read.isExpired(job, 0, Date.parse(end)), true);
  assert.equal(Read.isExpired({ ...job, status: "running" }, 0, Date.parse(end)), true);
  assert.equal(Read.isExpired({ ...job, status: "done" }, 0, Date.parse(end) + 100000), false);
  assert.equal(Read.deadline({ created_at: created }), Date.parse(created) + Read.JOB_TIMEOUT_MS);
  assert.equal(Read.deadline({}, 5000), 5000 + Read.JOB_TIMEOUT_MS);
  assert.equal(Read.sameVersion({ content_revision: 4 }, 4), true);
  assert.equal(Read.sameVersion({ content_revision: 5 }, 4), false);
  assert.equal(Read.sameVersion(null, 4), false);
  console.log("region-read timeout, abort, late reply and revision checks: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
