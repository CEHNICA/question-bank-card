"use strict";

// Run the actual page script with controlled network/body responses and iframe loads.
// The fake fetch deliberately ignores abort so stale-response protection is exercised.
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "practice.js"), "utf8");
const PAPER = "00000000-0000-4000-8000-000000000012";
const OTHER = "00000000-0000-4000-8000-000000000099";
const BASKET = `qb-practice-basket:${PAPER}`;
const OTHER_BASKET = `qb-practice-basket:${OTHER}`;
const formalBasket = JSON.stringify(["real-question"]);
const otherBasket = JSON.stringify(["other-demo-question"]);
const otherTeach = JSON.stringify({ paper: OTHER, version: 3, lesson: "review", completed: false });
const question = (id, number) => ({ id, number, fingerprint: `fingerprint-${id}`, content: { stem: `Question ${id}` } });
const items = [question("q1", 1), question("q2", 2)];
const pdf = { type: "application/pdf", size: 250 };
const flush = async () => { for (let i = 0; i < 18; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, fn, options = {}) {
    const entries = this.listeners.get(type) || [];
    entries.push({ fn, once: !!options.once }); this.listeners.set(type, entries);
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(entry => entry.fn !== fn));
  }
  dispatch(type, detail = {}) {
    const results = [];
    for (const entry of [...(this.listeners.get(type) || [])]) {
      if (entry.once) this.removeEventListener(type, entry.fn);
      results.push(entry.fn({ type, target: this, ...detail }));
    }
    return Promise.all(results);
  }
}

class Element extends Events {
  constructor(tag, harness) {
    super(); this.tagName = tag.toUpperCase(); this.harness = harness;
    this.children = []; this.attributes = new Map(); this.style = {};
    this.hidden = false; this.disabled = false; this.checked = false; this._text = "";
  }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parent = null; this.children = []; this._text = ""; this.append(...children); }
  querySelectorAll(selector) {
    assert.equal(selector, "input", "Only the script's real input selector is simulated");
    return this.children.flatMap(child => [...(child.tagName === "INPUT" ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  set srcdoc(value) { this.setAttribute("srcdoc", value); this.harness.frameSources.push(value); }
  get srcdoc() { return this.getAttribute("srcdoc") || ""; }
  click() {
    if (this.disabled) return Promise.resolve([]);
    if (this.tagName === "A") this.harness.downloads.push({ href: this.href, filename: this.download });
    return this.dispatch("click");
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
}

function setup({ teach = otherTeach, selected = ["q1"] } = {}) {
  const h = { pending: [], frameSources: [], downloads: [], urls: [], revoked: [], writes: [], renders: [], now: 0 };
  const controls = new Map(), timers = new Map(); let timerId = 0;
  const get = id => {
    if (!controls.has(id)) controls.set(id, new Element(id === "practiceFrame" ? "iframe" : "div", h));
    return controls.get(id);
  };
  for (const id of ["practiceReturn", "practiceError", "practiceRetry", "practiceList", "practiceCount", "practicePreview", "practiceExport",
    "practicePreviewPanel", "practiceFrame", "practiceDone", "stepSelect", "stepPreview", "stepExport", "practiceGuidance", "practicePages"]) get(id);
  for (const id of ["practiceError", "practiceRetry", "practiceExport", "practicePreviewPanel", "practiceDone"]) get(id).hidden = true;
  get("practicePreview").disabled = true; get("practiceExport").disabled = true;
  get("stepSelect").setAttribute("aria-current", "step");
  const frame = get("practiceFrame");
  frame.contentWindow = {}; frame.contentDocument = { documentElement: { scrollHeight: 600 } };
  const storage = new Map([["qb-basket", formalBasket], [OTHER_BASKET, otherBasket], ["qb-teach", teach], [BASKET, JSON.stringify(selected)]]);
  const window = new Events(); window.location = { pathname: `/practice/${PAPER}` };
  window.QBRender = { renderQuestion(element, content, options) { h.renders.push({ content, options }); element.textContent = content.stem; } };
  const body = new Element("body", h);
  const context = {
    window, document: { getElementById: get, createElement: tag => new Element(tag, h), body },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem(key, value) { h.writes.push({ key, value }); storage.set(key, value); } },
    fetch(url, options) { const wait = deferred(); h.pending.push({ url, options, ...wait }); return wait.promise; },
    AbortController, Promise, Set, Map, JSON, Object, Number, Error,
    Date: { now: () => h.now },
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { at: h.now + ms, fn }); return id; },
    clearTimeout: id => timers.delete(id),
    URL: { createObjectURL(data) { const url = `blob:practice-test-${h.urls.length}`; h.urls.push({ data, url }); return url; }, revokeObjectURL(url) { h.revoked.push(url); } }
  };
  vm.runInNewContext(source, context, { filename: "practice.js" });
  Object.assign(h, {
    get, frame, window, storage,
    async advance(ms) {
      const end = h.now + ms;
      for (;;) {
        const next = [...timers.entries()].filter(([, task]) => task.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        h.now = next[1].at; timers.delete(next[0]); next[1].fn(); await flush();
      }
      h.now = end; await flush();
    },
    respond(index, values = {}) {
      const result = { ok: values.ok ?? true, headers: { get: name => name === "X-Page-Count" ? String(values.pages ?? 1) : null } };
      for (const kind of ["json", "text", "blob"]) result[kind] = () => Promise.resolve(values[kind]);
      h.pending[index].resolve(result);
    },
    async load(data = items, index = h.pending.length - 1) { h.respond(index, { json: { items: data } }); await flush(); },
    async choose(index, checked) {
      const box = get("practiceList").querySelectorAll("input")[index];
      assert.equal(box.disabled, false, "Selection changes use an enabled user checkbox");
      box.checked = checked; await box.dispatch("change"); await flush();
    },
    async previewReady(pages = 1) {
      const work = get("practicePreview").click(); await flush();
      h.respond(h.pending.length - 1, { text: `<p>Preview ${pages}</p>` }); await flush();
      frame.contentWindow.__qbPdfStatus = { ready: true, page_count: pages };
      await frame.dispatch("load"); await h.advance(100); await work;
      assert.equal(get("practiceExport").hidden, false);
    },
    isolated(expectedTeach = teach) {
      assert.equal(storage.get("qb-basket"), formalBasket, "Practice never mutates the formal basket");
      assert.equal(storage.get(OTHER_BASKET), otherBasket, "Practice never mutates another paper's demo basket");
      assert.equal(storage.get("qb-teach"), expectedTeach, "Practice never overwrites another paper/version's tutorial progress");
      assert.ok(h.writes.every(write => [BASKET, "qb-teach"].includes(write.key)), "No unrelated storage namespace is written");
      assert.ok(h.pending.every(request => request.url.startsWith(`/api/demo/${PAPER}/`)), "Only the current demo API is requested");
    },
    noCompletion() {
      assert.equal(h.urls.length, 0); assert.equal(h.downloads.length, 0);
      assert.equal(get("practiceDone").hidden, true);
      assert.equal(get("stepExport").getAttribute("aria-current"), null);
    }
  });
  return h;
}

async function staleGet(phase) {
  const h = setup(), late = deferred();
  if (phase === "body") { h.respond(0, { json: late.promise }); await flush(); }
  await h.window.dispatch("pagehide");
  if (phase === "headers") assert.equal(h.pending[0].options.signal.aborted, true);
  await h.window.dispatch("pageshow", { persisted: true }); await flush();
  assert.equal(h.pending.length, 2, "A cache restore starts a fresh library read");
  await h.load([question("q1", 10), question("new", 11)], 1);
  const latest = h.get("practiceList").textContent;
  if (phase === "body") late.resolve({ items: [question("stale", 99)] });
  else h.respond(0, { json: { items: [question("stale", 99)] } });
  await flush();
  assert.equal(h.get("practiceList").textContent, latest, "An old GET cannot replace the restored paper");
  assert.deepEqual(JSON.parse(h.storage.get(BASKET)), ["q1"], "An old GET cannot drop the surviving current selection");
  assert.equal(h.get("practiceError").hidden, true); h.noCompletion(); h.isolated();
  await h.window.dispatch("pageshow", { persisted: false });
  assert.equal(h.pending.length, 2, "The initial non-cache pageshow does not duplicate loading");
}

async function stalePreview(phase) {
  const h = setup(); await h.load();
  const work = h.get("practicePreview").click(); await flush(); const late = deferred();
  if (phase === "body") { h.respond(1, { text: late.promise }); await flush(); }
  await h.window.dispatch("pagehide");
  if (phase === "headers") assert.equal(h.pending[1].options.signal.aborted, true);
  await h.window.dispatch("pageshow", { persisted: true }); await flush();
  if (phase === "body") late.resolve("stale preview"); else h.respond(1, { text: "stale preview" });
  await work; await flush();
  assert.equal(h.frameSources.length, 0, "A late preview cannot navigate the restored iframe");
  assert.equal(h.get("practicePreviewPanel").hidden, true);
  assert.equal(h.get("practicePreview").disabled, true, "Old finally cannot unlock the new library request");
  await h.load(items, 2); assert.equal(h.get("practicePreview").disabled, false);
  h.noCompletion(); h.isolated();
}

async function oldFrameReady() {
  const h = setup(); await h.load();
  h.frame.contentWindow.__qbPdfStatus = { ready: true, page_count: 999 };
  const work = h.get("practicePreview").click(); await flush(); h.respond(1, { text: "new document" }); await flush();
  await h.advance(500);
  assert.equal(h.get("practiceExport").hidden, true, "Old ready status before the new frame load is ignored");
  assert.equal(h.get("stepPreview").getAttribute("aria-current"), null);
  assert.equal(h.get("practicePages").textContent, "");
  h.frame.contentWindow.__qbPdfStatus = { ready: false }; await h.frame.dispatch("load"); await h.advance(100);
  assert.equal(h.get("practiceExport").hidden, true, "A loaded frame must still finish its new layout");
  h.frame.contentWindow.__qbPdfStatus = { ready: true, page_count: 2 }; await h.advance(100); await work;
  assert.equal(h.get("practicePages").textContent, "1 题 · 2 页");
  assert.equal(h.get("practiceExport").hidden, false); h.noCompletion(); h.isolated();
}

async function staleFramePoll() {
  const h = setup(); await h.load();
  const work = h.get("practicePreview").click(); await flush(); h.respond(1, { text: "old pending frame" }); await flush();
  h.frame.contentWindow.__qbPdfStatus = { ready: false }; await h.frame.dispatch("load");
  await h.window.dispatch("pagehide"); await h.window.dispatch("pageshow", { persisted: true }); await flush();
  h.frame.contentWindow.__qbPdfStatus = { ready: true, page_count: 7 }; await h.advance(100); await work;
  assert.equal(h.frame.srcdoc, ""); assert.equal(h.get("practicePreviewPanel").hidden, true);
  assert.equal(h.get("practiceExport").hidden, true); assert.equal(h.get("practicePreview").disabled, true);
  assert.equal(h.get("practiceError").hidden, true, "The old frame rejection does not show an error on the new load");
  await h.load(items, 2); h.noCompletion(); h.isolated();
}

async function selectionInvalidates() {
  const h = setup(); await h.load(); await h.previewReady(1);
  await h.choose(1, true);
  assert.equal(h.get("practicePreviewPanel").hidden, true); assert.equal(h.frame.srcdoc, "");
  assert.equal(h.get("practiceExport").hidden, true); assert.equal(h.get("practiceExport").disabled, true);
  assert.equal(h.get("stepSelect").getAttribute("aria-current"), "step");
  assert.equal(h.get("stepPreview").getAttribute("aria-current"), null);
  assert.deepEqual(JSON.parse(h.storage.get(BASKET)), ["q1", "q2"]);
  await h.get("practiceExport").click(); assert.equal(h.pending.length, 2, "A changed selection cannot export an old preview");
  // The old document remains accessible until a new navigation commits, as in a browser.
  await h.previewReady(2);
  assert.deepEqual(JSON.parse(h.pending[2].options.body).ids, ["q1", "q2"]);
  assert.equal(h.get("practicePages").textContent, "2 题 · 2 页"); h.noCompletion(); h.isolated();
}

async function staleDownload(phase) {
  const h = setup(); await h.load(); await h.previewReady(1);
  const work = h.get("practiceExport").click(); await flush(); const late = deferred();
  if (phase === "body") { h.respond(2, { blob: late.promise, pages: 1 }); await flush(); }
  await h.window.dispatch("pagehide");
  if (phase === "headers") assert.equal(h.pending[2].options.signal.aborted, true);
  assert.equal(h.get("practiceExport").hidden, true); assert.equal(h.get("practicePreviewPanel").hidden, true);
  await h.window.dispatch("pageshow", { persisted: true }); await flush();
  if (phase === "body") late.resolve(pdf); else h.respond(2, { blob: pdf, pages: 1 });
  await work; await flush(); h.noCompletion(); h.isolated();
  assert.equal(h.get("practicePreview").disabled, true, "Old download finally cannot unlock the cache-restore load");
  await h.load(items, 3); assert.equal(h.get("practicePreview").disabled, false);
}

async function successfulFlow() {
  const initial = JSON.stringify({ paper: PAPER, version: 4, lesson: "review", completed: false, retained: "keep" });
  const h = setup({ teach: initial }); await h.load(); await h.choose(1, true);
  assert.equal(JSON.parse(h.storage.get("qb-teach")).lesson, "basket");
  await h.previewReady(2); assert.equal(JSON.parse(h.storage.get("qb-teach")).lesson, "export");
  const work = h.get("practiceExport").click(); await flush(); h.respond(2, { blob: pdf, pages: 2 }); await work;
  assert.equal(h.downloads.length, 1); assert.equal(h.downloads[0].filename, "新手练习卷.pdf");
  assert.equal(h.get("practiceDone").hidden, false); assert.equal(h.get("stepExport").getAttribute("aria-current"), "step");
  const progress = JSON.parse(h.storage.get("qb-teach"));
  assert.equal(progress.completed, true); assert.equal(progress.lesson, "finish"); assert.equal(progress.retained, "keep");
  h.isolated(h.storage.get("qb-teach"));
  assert.deepEqual(JSON.parse(h.pending[2].options.body), { ids: ["q1", "q2"], fingerprints: { q1: "fingerprint-q1", q2: "fingerprint-q2" } });
  assert.equal(h.pending[2].options.headers["X-QB-Request"], "1");
  await h.advance(30000); assert.deepEqual(h.revoked, [h.downloads[0].href]);
  await h.window.dispatch("pagehide"); assert.equal(h.get("practiceDone").hidden, true);
  assert.equal(JSON.parse(h.storage.get("qb-teach")).completed, true, "Leaving does not erase a genuinely completed lesson");
}

async function wrongTutorialVersion() {
  const teach = JSON.stringify({ paper: PAPER, version: 2, lesson: "legacy", completed: false });
  const h = setup({ teach }); await h.load(); await h.choose(1, true); await h.previewReady(1);
  const work = h.get("practiceExport").click(); await flush(); h.respond(2, { blob: pdf, pages: 1 }); await work;
  assert.equal(h.downloads.length, 1); h.isolated();
}

(async () => {
  const cases = [
    ["late GET headers", () => staleGet("headers")], ["late GET body", () => staleGet("body")],
    ["late preview headers", () => stalePreview("headers")], ["late preview body", () => stalePreview("body")],
    ["old iframe ready before load", oldFrameReady], ["late iframe layout", staleFramePoll],
    ["selection invalidates preview", selectionInvalidates],
    ["late PDF headers", () => staleDownload("headers")], ["late PDF body", () => staleDownload("body")],
    ["valid preview and PDF finish", successfulFlow], ["different tutorial version is preserved", wrongTutorialVersion]
  ];
  for (const [name, test] of cases) { await test(); console.log(`Practice lifecycle: ${name}: OK`); }
  console.log(`Practice lifecycle: ${cases.length} behavior cases passed against the actual practice.js`);
})().catch(error => { console.error(error); process.exitCode = 1; });
