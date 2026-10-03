"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "export-settings.js"), "utf8");
const flush = async () => { for (let index = 0; index < 20; index++) await Promise.resolve(); };

function setup({ desktop = true, directory = "" } = {}) {
  const elements = new Map(), calls = [], timers = new Map(); let nextTimer = 0;
  class Element {
    constructor() { this.value = ""; this.disabled = false; this.hidden = false; this.readOnly = false; this.textContent = ""; this.listeners = new Map(); }
    set innerHTML(value) {
      this.html = value;
      for (const match of value.matchAll(/<[^>]+id="([^"]+)"[^>]*>/g)) {
        const child = new Element(); child.disabled = /\sdisabled(?:\s|>)/.test(match[0]); child.hidden = /\shidden(?:\s|>)/.test(match[0]); child.readOnly = /\sreadonly(?:\s|>)/.test(match[0]);
        elements.set(match[1], child);
      }
    }
    addEventListener(name, listener) { this.listeners.set(name, listener); }
    click() { this.listeners.get("click")?.(); }
    change(value) { this.value = value; this.listeners.get("change")?.(); }
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
    async click(id) { elements.get(id).click(); await flush(); },
    async mode(value) { elements.get("exportDirectoryMode").change(value); await flush(); } };
}

const ids = ["exportDirectorySelect", "exportDirectoryOpen", "exportDirectorySave", "exportDirectoryCancel", "exportDirectoryRetry"];
const visible = (app, expected) => {
  const shown = ids.filter(id => !app.get(id).hidden);
  assert.ok(shown.length <= 2, "Every state exposes at most two action buttons");
  assert.deepEqual(shown, expected);
};
const savedButtons = ["exportDirectorySelect", "exportDirectoryOpen"];
const draftButtons = ["exportDirectorySave", "exportDirectoryCancel"];
const retryButton = ["exportDirectoryRetry"];
const target = "C:\\offline-fixture\\exports", picked = "C:\\offline-fixture\\new-output";
async function pick(app, value = picked) {
  app.response(request => {
    assert.equal(request.url, "/api/export-preferences/select"); assert.equal(request.method, "POST");
    assert.deepEqual(request.payload, {}); return { body: { selected: true, directory: value } };
  });
  await app.click("exportDirectorySelect");
}

(async () => {
  const normal = setup(); await normal.window.ExportSettings.refresh(); assert.equal(normal.calls.length, 0);
  await normal.mount();
  assert.equal(normal.calls.length, 1); assert.equal(normal.calls[0].method, "GET");
  assert.equal(normal.calls[0].url, "/api/export-preferences"); assert.equal(normal.calls[0].cache, "no-store");
  assert.equal(normal.calls[0].headers["X-QB-Request"], "1"); assert.equal(normal.calls[0].body, undefined);
  assert.equal(normal.get("exportDirectory").readOnly, true, "The folder is selected rather than requiring a typed path");
  assert.equal(normal.get("exportDirectoryMode").value, "browser"); assert.equal(normal.get("exportDirectoryField").hidden, true);
  visible(normal, ["exportDirectorySelect"]); assert.equal(normal.timers.size, 0);
  await normal.window.ExportSettings.mount(new normal.host.constructor()); assert.equal(normal.calls.length, 1);
  await normal.click("exportDirectoryRetry"); assert.equal(normal.calls.length, 1, "A hidden successful-load retry cannot initiate another read");
  await pick(normal);
  visible(normal, draftButtons); assert.equal(normal.get("exportDirectory").value, picked);
  assert.equal(normal.get("exportDirectoryMode").value, "folder"); assert.equal(normal.get("exportDirectoryField").hidden, false);
  assert.equal(normal.current.directory, ""); assert.equal(normal.calls.length, 2, "Native choice does not write preferences");
  const beforeCancel = normal.calls.length; await normal.click("exportDirectoryCancel");
  assert.equal(normal.current.directory, ""); assert.equal(normal.calls.length, beforeCancel); visible(normal, ["exportDirectorySelect"]);

  const chooser = setup({ directory: target }); await chooser.mount(); visible(chooser, savedButtons);
  assert.equal(chooser.get("exportDirectorySelect").textContent, "更改位置");
  chooser.response(() => ({ body: { selected: false, cancelled: true } })); await chooser.click("exportDirectorySelect");
  assert.equal(chooser.get("exportDirectory").value, target); assert.equal(chooser.current.directory, target); visible(chooser, savedButtons);
  for (const result of [{ ok: false, body: { error: "offline chooser failed" } }, { badJSON: true },
                        { body: { selected: true, directory: 42 } }, { body: { selected: true, directory: "" } }, { body: { selected: false } }]) {
    chooser.response(() => result); await chooser.click("exportDirectorySelect");
    assert.equal(chooser.get("exportDirectory").value, target); assert.equal(chooser.current.directory, target);
    visible(chooser, savedButtons); assert.equal(chooser.timers.size, 0);
  }
  let chooseDone;
  chooser.response(() => new Promise(resolve => { chooseDone = resolve; })); await chooser.click("exportDirectorySelect");
  for (const id of ids) assert.equal(chooser.get(id).disabled, true);
  assert.equal(chooser.timers.size, 0, "The native picker has no 15-second network deadline");
  const activeCalls = chooser.calls.length;
  for (const id of ids) await chooser.click(id);
  await chooser.mode("browser"); await chooser.window.ExportSettings.refresh();
  assert.equal(chooser.calls.length, activeCalls, "Busy selection prevents competing reads, mode changes, and writes");
  chooseDone({ body: { selected: false, cancelled: true } }); await flush(); visible(chooser, savedButtons);
  await pick(chooser); visible(chooser, draftButtons);
  const draftCalls = chooser.calls.length;
  await chooser.click("exportDirectorySelect"); await chooser.click("exportDirectoryOpen");
  assert.equal(chooser.calls.length, draftCalls, "Hidden stable-state operations cannot act on a draft");
  chooser.response(request => { assert.equal(request.url, "/api/export-preferences"); assert.equal(request.method, "GET"); return { body: { ...chooser.current } }; });
  await chooser.window.ExportSettings.refresh();
  assert.equal(chooser.get("exportDirectory").value, picked); visible(chooser, draftButtons);
  assert.match(chooser.get("exportDirectoryStatus").textContent, /保存后生效/);
  chooser.response(request => {
    assert.equal(request.method, "POST"); assert.equal(request.headers["Content-Type"], "application/json");
    assert.deepEqual(request.payload, { directory: picked }); chooser.current.directory = picked; return { body: { ...chooser.current } };
  });
  await chooser.click("exportDirectorySave"); assert.equal(chooser.current.directory, picked); visible(chooser, savedButtons);
  const savedCalls = chooser.calls.length; await chooser.click("exportDirectorySave"); assert.equal(chooser.calls.length, savedCalls);
  chooser.response(request => { assert.equal(request.url, "/api/export-preferences/open"); assert.deepEqual(request.payload, { target: "directory" }); return { body: { opened: true } }; });
  await chooser.click("exportDirectoryOpen"); visible(chooser, savedButtons); assert.equal(chooser.get("exportDirectory").value, picked);

  const modes = setup({ directory: target }); await modes.mount();
  const beforeMode = modes.calls.length; await modes.mode("browser");
  assert.equal(modes.calls.length, beforeMode, "Changing download mode only stages the preference");
  assert.equal(modes.current.directory, target); assert.equal(modes.get("exportDirectoryField").hidden, true); visible(modes, draftButtons);
  await modes.click("exportDirectoryCancel"); assert.equal(modes.get("exportDirectoryMode").value, "folder"); visible(modes, savedButtons);
  await modes.mode("browser"); modes.response(request => { assert.deepEqual(request.payload, { directory: "" }); modes.current.directory = ""; return { body: { ...modes.current } }; });
  await modes.click("exportDirectorySave"); assert.equal(modes.current.directory, ""); visible(modes, ["exportDirectorySelect"]);
  modes.response(() => ({ body: { selected: false, cancelled: true } })); await modes.mode("folder");
  assert.equal(modes.get("exportDirectoryMode").value, "browser", "Cancelling a mode-triggered picker restores the saved mode");
  visible(modes, ["exportDirectorySelect"]);

  const web = setup({ desktop: false, directory: target }); await web.mount();
  assert.equal(web.get("exportDirectoryOptions").hidden, true); visible(web, []);
  for (const id of ids.slice(0, 4)) { assert.equal(web.get(id).disabled, true); await web.click(id); }
  await web.mode("folder"); assert.equal(web.calls.length, 1, "Web mode has no desktop mutation or Explorer operation");
  assert.match(web.get("exportDirectoryStatus").textContent, /网页版/);

  const failed = setup(); failed.response(() => { throw Error("offline transport unavailable"); }); await failed.mount();
  visible(failed, retryButton); assert.match(failed.get("exportDirectoryStatus").textContent, /offline transport/);
  assert.doesNotMatch(failed.get("exportDirectoryStatus").textContent, /目前使用|恢复默认/);
  failed.response(() => ({ body: { ...failed.current } })); await failed.click("exportDirectoryRetry"); visible(failed, ["exportDirectorySelect"]);
  const incomplete = setup({ directory: target }); incomplete.response(() => ({ body: { directory: target } })); await incomplete.mount();
  visible(incomplete, retryButton); assert.match(incomplete.get("exportDirectoryStatus").textContent, /不完整/);

  const retained = setup({ directory: target }); await retained.mount(); await pick(retained);
  retained.response(() => ({ body: { directory: "", desktop_capable: true, warning: "offline damaged preferences; retained" } }));
  await retained.window.ExportSettings.refresh(); visible(retained, retryButton);
  assert.equal(retained.get("exportDirectory").value, picked); assert.equal(retained.get("exportDirectoryMode").value, "folder");
  assert.match(retained.get("exportDirectoryStatus").textContent, /damaged/); assert.doesNotMatch(retained.get("exportDirectoryStatus").textContent, /浏览器下载/);
  const blocked = retained.calls.length; await retained.click("exportDirectorySave"); await retained.mode("browser"); assert.equal(retained.calls.length, blocked);
  retained.response(() => ({ body: { ...retained.current } })); await retained.click("exportDirectoryRetry");
  assert.equal(retained.get("exportDirectory").value, picked); visible(retained, draftButtons);
  const external = "C:\\offline-fixture\\changed-elsewhere";
  retained.current.directory = external; await retained.window.ExportSettings.refresh();
  assert.equal(retained.get("exportDirectory").value, picked, "Automatic rereads cannot overwrite a candidate even when the saved value changes");
  await retained.click("exportDirectoryCancel"); assert.equal(retained.get("exportDirectory").value, external); visible(retained, savedButtons);

  const rejected = setup({ directory: target }); await rejected.mount(); await pick(rejected);
  rejected.response(() => ({ ok: false, body: { error: "offline folder not writable" } }));
  const rejectionCalls = rejected.calls.length; await rejected.click("exportDirectorySave");
  assert.equal(rejected.calls.length, rejectionCalls + 1, "An explicit server rejection does not need uncertain-save recovery");
  assert.equal(rejected.get("exportDirectory").value, picked); assert.equal(rejected.current.directory, target); visible(rejected, draftButtons);
  assert.match(rejected.get("exportDirectoryStatus").textContent, /not writable/); assert.equal(rejected.timers.size, 0);

  for (const result of [{ badJSON: true }, { body: { directory: 42, desktop_capable: true } }, { body: { directory: "", desktop_capable: true } }]) {
    const uncertain = setup({ directory: target }); await uncertain.mount(); await pick(uncertain);
    uncertain.response(request => request.method === "POST" ? result : { body: { ...uncertain.current } });
    const count = uncertain.calls.length; await uncertain.click("exportDirectorySave");
    assert.equal(uncertain.calls.length, count + 2, "Malformed replies trigger one read, never an automatic second POST");
    assert.equal(uncertain.get("exportDirectory").value, picked); assert.equal(uncertain.get("exportDirectoryMode").value, "folder");
    assert.equal(uncertain.current.directory, target); visible(uncertain, draftButtons);
    assert.match(uncertain.get("exportDirectoryStatus").textContent, /所选位置仍保留/);
  }
  const committed = setup({ directory: target }); await committed.mount(); await pick(committed);
  committed.response(request => {
    if (request.method === "POST") { committed.current.directory = picked; return { badJSON: true }; }
    return { body: { ...committed.current } };
  });
  await committed.click("exportDirectorySave"); visible(committed, savedButtons);
  assert.equal(committed.get("exportDirectory").value, picked); assert.match(committed.get("exportDirectoryStatus").textContent, /已保存/);

  const unknown = setup({ directory: target }); await unknown.mount(); await pick(unknown);
  unknown.response(request => { if (request.method === "POST") return { badJSON: true }; throw Error("offline confirmation unavailable"); });
  await unknown.click("exportDirectorySave"); visible(unknown, retryButton);
  assert.equal(unknown.get("exportDirectory").value, picked); assert.equal(unknown.get("exportDirectoryRetry").textContent, "确认保存结果");
  assert.match(unknown.get("exportDirectoryStatus").textContent, /保存结果尚未确认/);
  unknown.response(() => ({ body: { directory: picked, desktop_capable: true } })); await unknown.click("exportDirectoryRetry");
  visible(unknown, savedButtons); assert.match(unknown.get("exportDirectoryStatus").textContent, /已保存/);

  const waiting = setup(); let complete;
  waiting.response(() => new Promise(resolve => { complete = resolve; })); const loading = waiting.mount(); await flush();
  visible(waiting, []); assert.equal(waiting.get("exportDirectoryMode").disabled, true);
  const firstCalls = waiting.calls.length; await waiting.window.ExportSettings.refresh(); await waiting.click("exportDirectoryRetry");
  assert.equal(waiting.calls.length, firstCalls);
  const deadline = [...waiting.timers.values()][0]; assert.equal(deadline.delay, 15000); deadline.fn(); await loading;
  visible(waiting, retryButton); assert.match(waiting.get("exportDirectoryStatus").textContent, /超时/);
  complete({ body: { directory: target, desktop_capable: true } }); await flush();
  assert.equal(waiting.get("exportDirectoryOptions").hidden, true, "An aborted read's late response cannot initialize stale desktop controls");
  waiting.current.directory = target; waiting.response(() => ({ body: { ...waiting.current } })); await waiting.click("exportDirectoryRetry"); await pick(waiting);
  waiting.response(request => request.method === "GET" ? { body: { ...waiting.current } } : new Promise(resolve => { complete = resolve; }));
  await waiting.click("exportDirectorySave"); visible(waiting, draftButtons); assert.equal(waiting.get("exportDirectorySave").disabled, true);
  const beforeRepeat = waiting.calls.length;
  for (const id of ids) await waiting.click(id); await waiting.mode("browser"); await waiting.window.ExportSettings.refresh();
  assert.equal(waiting.calls.length, beforeRepeat);
  [...waiting.timers.values()][0].fn(); await flush(); visible(waiting, draftButtons);
  assert.equal(waiting.get("exportDirectory").value, picked); assert.match(waiting.get("exportDirectoryStatus").textContent, /所选位置仍保留/);
  complete({ body: { directory: "C:\\offline-fixture\\obsolete-response", desktop_capable: true } }); await flush();
  assert.equal(waiting.get("exportDirectory").value, picked, "A lost POST's late response cannot overwrite the retained choice");
  assert.equal(waiting.timers.size, 0);
  console.log("Export settings: two-button states, explicit folder/mode save and cancel, draft-preserving rereads, web hiding, fault-only retry, corrupt/uncertain preferences, native busy/cancel and late replies: OK");
})().catch(error => { console.error(error); process.exitCode = 1; });
