"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "export-settings.js"), "utf8");
const flush = async () => { for (let index = 0; index < 20; index++) await Promise.resolve(); };

function setup({ desktop = true, directory = "" } = {}) {
  const elements = new Map(), calls = [], timers = new Map(); let nextTimer = 0;
  class Element {
    constructor() { this.value = ""; this.disabled = false; this.hidden = false; this.textContent = ""; this.listeners = new Map(); }
    set innerHTML(value) {
      this.html = value;
      for (const match of value.matchAll(/<[^>]+id="([^"]+)"[^>]*>/g)) {
        const child = new Element(); child.disabled = /\sdisabled(?:\s|>)/.test(match[0]); child.hidden = /\shidden(?:\s|>)/.test(match[0]);
        elements.set(match[1], child);
      }
    }
    addEventListener(name, listener) { this.listeners.set(name, listener); }
    click() { this.listeners.get("click")?.(); }
  }
  const current = { directory, desktop_capable: desktop, warning: "" };
  let response = () => ({ body: { ...current }, ok: true });
  const window = {}, host = new Element();
  const context = { window, AbortController, document: { getElementById: id => elements.get(id) },
    setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; }, clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      const request = { url, ...options, payload: options.body ? JSON.parse(options.body) : undefined }; calls.push(request);
      const result = new Promise((resolve, reject) => {
        options.signal.addEventListener("abort", () => { const error = new Error("offline abort"); error.name = "AbortError"; reject(error); }, { once: true });
        Promise.resolve().then(() => response(request)).then(resolve, reject);
      });
      const resultBody = await result;
      return { ok: resultBody.ok !== false, json: async () => { if (resultBody.badJSON) throw Error("offline JSON parse failed"); return resultBody.body; } };
    }
  };
  vm.runInNewContext(source, context);
  return { window, host, current, calls, timers, get: id => elements.get(id),
    response: value => { response = value; }, mount: () => window.ExportSettings.mount(host),
    async click(id) { elements.get(id).click(); await flush(); } };
}

(async () => {
  const normal = setup(); await normal.mount();
  assert.equal(normal.calls.length, 1); assert.equal(normal.calls[0].method, "GET");
  assert.equal(normal.calls[0].url, "/api/export-preferences"); assert.equal(normal.calls[0].cache, "no-store");
  assert.equal(normal.calls[0].headers["X-QB-Request"], "1"); assert.equal(normal.calls[0].body, undefined);
  assert.equal(normal.get("exportDirectory").disabled, false); assert.equal(normal.get("exportDirectoryOpen").disabled, true);
  assert.equal(normal.get("exportDirectorySelect").disabled, false);
  assert.equal(normal.get("exportDirectoryRetry").hidden, false, "Reloading saved directory remains available after a successful load"); assert.equal(normal.timers.size, 0);
  await normal.window.ExportSettings.mount(new (normal.host.constructor)()); assert.equal(normal.calls.length, 1, "Repeated mount must not duplicate requests or controls");

  const target = "C:\\offline-fixture\\exports";
  normal.get("exportDirectory").value = `  ${target}  `;
  normal.response(request => { assert.equal(request.method, "POST"); assert.equal(request.headers["Content-Type"], "application/json");
    assert.deepEqual(request.payload, { directory: target }); Object.assign(normal.current, request.payload); return { body: { ...normal.current } }; });
  await normal.click("exportDirectorySave");
  assert.equal(normal.get("exportDirectory").value, target); assert.equal(normal.get("exportDirectoryOpen").disabled, false);
  assert.equal(normal.get("exportDirectorySave").disabled, false); assert.equal(normal.timers.size, 0);
  normal.get("exportDirectory").value = "C:\\offline-fixture\\unsaved-directory";
  normal.response(request => { assert.equal(request.url, "/api/export-preferences/open"); assert.deepEqual(request.payload, { target: "directory" }); return { body: { opened: true } }; });
  await normal.click("exportDirectoryOpen");
  assert.equal(normal.get("exportDirectory").value, "C:\\offline-fixture\\unsaved-directory", "Opening uses the saved target without implicitly saving typed changes");
  normal.response(request => { assert.deepEqual(request.payload, { directory: "" }); normal.current.directory = ""; return { body: { ...normal.current } }; });
  await normal.click("exportDirectoryReset"); assert.equal(normal.get("exportDirectory").value, ""); assert.equal(normal.get("exportDirectoryOpen").disabled, true);

  const chooser = setup({ directory: target }); await chooser.mount();
  const picked = "C:\\offline-fixture\\已选择的新位置";
  chooser.response(request => { assert.equal(request.url, "/api/export-preferences/select"); assert.equal(request.method, "POST");
    assert.deepEqual(request.payload, {}); return { body: { selected: true, directory: picked } }; });
  await chooser.click("exportDirectorySelect");
  assert.equal(chooser.get("exportDirectory").value, picked); assert.equal(chooser.current.directory, target);
  assert.equal(chooser.calls.length, 2, "A native choice only fills the candidate, without a preference POST");
  assert.match(chooser.get("exportDirectoryStatus").textContent, /保存位置.*生效/);
  chooser.response(() => ({ body: { selected: false, cancelled: true } })); await chooser.click("exportDirectorySelect");
  assert.equal(chooser.get("exportDirectory").value, picked, "Cancel retains both saved location and the prior unsaved candidate");
  assert.equal(chooser.current.directory, target); assert.match(chooser.get("exportDirectoryStatus").textContent, /取消/);
  for (const result of [{ ok: false, body: { error: "offline chooser failed" } }, { badJSON: true },
                        { body: { selected: true, directory: 42 } }, { body: { selected: false } }]) {
    chooser.response(() => result); await chooser.click("exportDirectorySelect");
    assert.equal(chooser.get("exportDirectory").value, picked); assert.equal(chooser.current.directory, target);
    assert.equal(chooser.get("exportDirectorySelect").disabled, false); assert.equal(chooser.timers.size, 0);
  }
  let chooseDone;
  chooser.response(() => new Promise(resolve => { chooseDone = resolve; })); await chooser.click("exportDirectorySelect");
  assert.equal(chooser.get("exportDirectorySelect").disabled, true); assert.equal(chooser.get("exportDirectorySave").disabled, true);
  assert.equal(chooser.timers.size, 0, "A native file choice may remain open longer than 15 seconds without an HTTP deadline");
  const activeChoiceCalls = chooser.calls.length;
  for (const id of ["exportDirectorySelect", "exportDirectorySave", "exportDirectoryReset", "exportDirectoryOpen", "exportDirectoryRetry"]) await chooser.click(id);
  assert.equal(chooser.calls.length, activeChoiceCalls, "A pending picker prevents repeated dialogs or competing saves/resets/reloads");
  chooseDone({ body: { selected: false, cancelled: true } }); await flush();
  assert.equal(chooser.get("exportDirectorySelect").disabled, false); assert.equal(chooser.get("exportDirectory").value, picked);
  chooser.response(request => { assert.equal(request.url, "/api/export-preferences"); assert.deepEqual(request.payload, { directory: picked });
    chooser.current.directory = picked; return { body: { ...chooser.current } }; });
  await chooser.click("exportDirectorySave"); assert.equal(chooser.current.directory, picked);

  const web = setup({ desktop: false, directory: target }); await web.mount();
  for (const id of ["exportDirectory", "exportDirectorySelect", "exportDirectorySave", "exportDirectoryReset", "exportDirectoryOpen"]) assert.equal(web.get(id).disabled, true);
  for (const id of ["exportDirectorySelect", "exportDirectorySave", "exportDirectoryReset", "exportDirectoryOpen"]) await web.click(id);
  assert.equal(web.calls.length, 1, "Forged events cannot save or open a directory in web mode");
  assert.match(web.get("exportDirectoryStatus").textContent, /网页版/);

  const failedLoad = setup(); failedLoad.response(() => { throw Error("offline transport unavailable"); }); await failedLoad.mount();
  assert.match(failedLoad.get("exportDirectoryStatus").textContent, /offline transport/);
  assert.equal(failedLoad.get("exportDirectoryRetry").hidden, false); assert.equal(failedLoad.get("exportDirectoryRetry").disabled, false);
  assert.equal(failedLoad.get("exportDirectorySave").disabled, true); assert.equal(failedLoad.timers.size, 0);
  failedLoad.response(() => ({ body: { ...failedLoad.current } })); await failedLoad.click("exportDirectoryRetry");
  assert.equal(failedLoad.get("exportDirectorySave").disabled, false);

  const invalid = setup(); invalid.response(() => ({ body: { directory: target } })); await invalid.mount();
  assert.match(invalid.get("exportDirectoryStatus").textContent, /不完整/); assert.equal(invalid.get("exportDirectoryRetry").hidden, false);
  invalid.response(() => ({ body: { ...invalid.current } })); await invalid.click("exportDirectoryRetry");
  assert.equal(invalid.get("exportDirectory").disabled, false);

  const errors = setup({ directory: target }); await errors.mount();
  const typed = "C:\\offline-fixture\\new-output";
  for (const result of [{ ok: false, body: { error: "offline folder not writable" } }, { badJSON: true }, { body: { directory: 42, desktop_capable: true } }]) {
    errors.get("exportDirectory").value = typed; errors.response(() => result); await errors.click("exportDirectorySave");
    assert.equal(errors.get("exportDirectory").value, typed, "HTTP, JSON and malformed-state failures preserve the unsubmitted input");
    assert.equal(errors.get("exportDirectorySave").disabled, false); assert.equal(errors.get("exportDirectoryOpen").disabled, false);
    assert.equal(errors.timers.size, 0, "Every rejected request releases its deadline and controls");
  }
  assert.equal(errors.get("exportDirectoryRetry").hidden, false, "Malformed save responses must offer rereading the actual saved location");
  errors.response(request => { assert.equal(request.method, "GET"); return { body: { ...errors.current } }; });
  await errors.click("exportDirectoryRetry");
  assert.equal(errors.get("exportDirectory").value, target, "Explicit reload restores saved directory metadata after an uncertain save response");
  assert.equal(errors.get("exportDirectoryOpen").disabled, false); assert.doesNotMatch(errors.get("exportDirectoryStatus").textContent, /不完整|未完成/);
  errors.response(() => ({ ok: false, body: { error: "offline Explorer failed" } })); await errors.click("exportDirectoryOpen");
  assert.match(errors.get("exportDirectoryStatus").textContent, /Explorer failed/); assert.equal(errors.get("exportDirectoryOpen").disabled, false);

  const waiting = setup(); let complete;
  waiting.response(() => new Promise(resolve => { complete = resolve; })); const loading = waiting.mount(); await flush();
  assert.equal(waiting.get("exportDirectory").disabled, true); assert.equal(waiting.get("exportDirectoryRetry").disabled, true);
  await waiting.click("exportDirectoryRetry"); await waiting.click("exportDirectorySave"); assert.equal(waiting.calls.length, 1);
  const deadline = [...waiting.timers.values()][0]; assert.equal(deadline.delay, 15000); deadline.fn(); await loading;
  assert.match(waiting.get("exportDirectoryStatus").textContent, /超时/); assert.equal(waiting.get("exportDirectoryRetry").disabled, false);
  complete({ body: { directory: target, desktop_capable: true } }); await flush();
  assert.equal(waiting.get("exportDirectory").value, "", "Late aborted loads cannot initialize stale controls");
  waiting.response(() => ({ body: { ...waiting.current } })); await waiting.click("exportDirectoryRetry");
  waiting.get("exportDirectory").value = typed; waiting.response(() => new Promise(resolve => { complete = resolve; }));
  await waiting.click("exportDirectorySave"); assert.equal(waiting.get("exportDirectorySave").disabled, true);
  const beforeRepeat = waiting.calls.length;
  await waiting.click("exportDirectorySave"); await waiting.click("exportDirectoryReset"); await waiting.click("exportDirectoryOpen");
  assert.equal(waiting.calls.length, beforeRepeat, "Busy operations cannot be duplicated or replaced by reset/open");
  [...waiting.timers.values()][0].fn(); await flush();
  assert.equal(waiting.get("exportDirectory").value, typed); assert.equal(waiting.get("exportDirectorySave").disabled, false);
  assert.match(waiting.get("exportDirectoryStatus").textContent, /结果.*未确认/); assert.doesNotMatch(waiting.get("exportDirectoryStatus").textContent, /保存失败/);
  complete({ body: { directory: "C:\\offline-fixture\\obsolete-response", desktop_capable: true } }); await flush();
  assert.equal(waiting.get("exportDirectory").value, typed, "A timed-out save's late response cannot replace the retained input");
  assert.equal(waiting.timers.size, 0);
  console.log("Export directory settings: native pick/cancel without implicit save or timeout, explicit save/reset/open, web guard, load/retry, failures, duplicate requests and retained input: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
