"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const layout = require("./exam-layout.js");
const examExport = require("./exam-export.js");

function makeFakeElement(tag, env, doc) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    nodeType: 1,
    ownerDocument: doc || null,
    className: "",
    style: {
      setProperty(k, v) {
        this[k] = v;
      },
      getPropertyValue(k) {
        return this[k] || "";
      },
    },
    dataset: {},
    attributes: {},
    children: [],
    _text: "",
    parentNode: null,
    parentElement: null,
    get firstElementChild() {
      return this.children[0] || null;
    },
    get lastElementChild() {
      return this.children.at(-1) || null;
    },
    get textContent() {
      return this.children.length ? this._text + this.children.map((c) => c.textContent).join("") : this._text;
    },
    set textContent(v) {
      this._text = String(v == null ? "" : v);
      this.children = [];
    },
    get classList() {
      const parse = () => new Set(String(el.className || "").split(/\s+/).filter(Boolean));
      return {
        add: (...names) => {
          const s = parse();
          names.forEach((n) => s.add(n));
          el.className = [...s].join(" ");
        },
        remove: (...names) => {
          const s = parse();
          names.forEach((n) => s.delete(n));
          el.className = [...s].join(" ");
        },
        contains: (name) => parse().has(name),
      };
    },
    setAttribute(k, v) {
      this.attributes[k] = String(v);
    },
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null;
    },
    removeAttribute(k) {
      delete this.attributes[k];
    },
    remove() {
      if (this.parentNode && Array.isArray(this.parentNode.children)) {
        const idx = this.parentNode.children.indexOf(this);
        if (idx >= 0) this.parentNode.children.splice(idx, 1);
      }
      this.parentNode = this.parentElement = null;
    },
    append(...nodes) {
      for (const n of nodes) {
        if (!n) continue;
        if (typeof n === "string") el._text += n;
        else {
          if (typeof n.remove === "function") n.remove();
          n.parentNode = n.parentElement = el;
          el.children.push(n);
        }
      }
    },
    prepend(...nodes) {
      for (const n of [...nodes].reverse()) {
        if (!n) continue;
        if (typeof n === "string") el._text = n + el._text;
        else {
          if (typeof n.remove === "function") n.remove();
          n.parentNode = n.parentElement = el;
          el.children.unshift(n);
        }
      }
    },
    before(node) {
      if (!node || !this.parentNode) return;
      if (typeof node.remove === "function") node.remove();
      const idx = this.parentNode.children.indexOf(this);
      if (idx >= 0) {
        node.parentNode = node.parentElement = this.parentNode;
        this.parentNode.children.splice(idx, 0, node);
      }
    },
    replaceChildren(...nodes) {
      this.children = [];
      this._text = "";
      this.append(...nodes);
    },
    matches(selector) {
      return String(selector)
        .split(",")
        .some((part) => {
          const t = part.trim();
          if (t.startsWith(".")) return String(this.className || "").split(/\s+/).includes(t.slice(1));
          return this.tagName === t.toUpperCase();
        });
    },
    cloneNode(deep) {
      const copy = makeFakeElement(this.tagName.toLowerCase(), env, this.ownerDocument);
      copy.className = this.className;
      copy.dataset = { ...this.dataset };
      copy.attributes = { ...this.attributes };
      copy.style = { ...this.style };
      copy._text = this._text;
      copy._height = this._height;
      copy._width = this._width;
      if (deep) {
        copy.children = this.children.map((c) => {
          const ch = c.cloneNode(true);
          ch.parentNode = ch.parentElement = copy;
          return ch;
        });
      }
      return copy;
    },
    querySelectorAll(selector) {
      const out = [];
      const walk = (node) => {
        for (const ch of node.children) {
          if (ch.matches && ch.matches(selector)) out.push(ch);
          walk(ch);
        }
      };
      walk(this);
      return out;
    },
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
    getBoundingClientRect() {
      const childH = this.children.reduce((s, c) => s + c.getBoundingClientRect().height, 0);
      const h = this._height != null ? this._height : childH || (this.className.includes("print-question") ? 460 : 32);
      const w = this._width != null ? this._width : env.colPx;
      return { width: w, height: h, left: 0, right: w, top: 0, bottom: h };
    },
    get scrollWidth() {
      return this.getBoundingClientRect().width;
    },
    get clientWidth() {
      return this.getBoundingClientRect().width;
    },
  };
  return el;
}

test("ExamLayout.pageSpec computes A4 and A3 2-column geometry accurately", () => {
  assert.deepEqual(layout.pageSpec({}), {
    paperSpec: "a4",
    sealLine: false,
    pageWidthMm: 210,
    pageHeightMm: 297,
    colWidthMm: 178,
    bodyHeightMm: 261,
    columns: 1,
    colGapMm: 0,
    sealMarginMm: 16,
    outerMarginMm: 16,
  });
  assert.deepEqual(layout.pageSpec({ paper_spec: "a4", seal_line: true }), {
    paperSpec: "a4",
    sealLine: true,
    pageWidthMm: 210,
    pageHeightMm: 297,
    colWidthMm: 168,
    bodyHeightMm: 261,
    columns: 1,
    colGapMm: 0,
    sealMarginMm: 28,
    outerMarginMm: 14,
  });
  assert.deepEqual(layout.pageSpec({ paper_spec: "a3_2col", seal_line: false }), {
    paperSpec: "a3_2col",
    sealLine: false,
    pageWidthMm: 420,
    pageHeightMm: 297,
    colWidthMm: 184,
    bodyHeightMm: 261,
    columns: 2,
    colGapMm: 16,
    sealMarginMm: 18,
    outerMarginMm: 18,
  });
  assert.deepEqual(layout.pageSpec({ paper_spec: "a3_2col", seal_line: true }), {
    paperSpec: "a3_2col",
    sealLine: true,
    pageWidthMm: 420,
    pageHeightMm: 297,
    colWidthMm: 180,
    bodyHeightMm: 261,
    columns: 2,
    colGapMm: 14,
    sealMarginMm: 30,
    outerMarginMm: 16,
  });
});

test("ExamExport scoring helpers format totals, headers, sections, and prefixes", () => {
  assert.equal(examExport.formatScore(5), "5");
  assert.equal(examExport.formatScore(5.5), "5.5");
  const items = [
    { id: "q1", type: "single_choice" },
    { id: "q2", type: "single_choice" },
    { id: "q3", type: "free_response" },
  ];
  const opts = {
    scoring_enabled: true,
    exam_duration_minutes: 120,
    type_scores: { single_choice: 5, free_response: 12 },
    question_scores: { q3: 15 },
    student_info: true,
  };
  assert.equal(examExport.totalExamScore(items, opts), 25);
  assert.equal(
    examExport.studentHeaderText(items, opts),
    "班级 ____________　姓名 ____________　考号 ____________　考试时间：120 分钟　满分：25 分"
  );
  assert.equal(
    examExport.sectionHeading(items.slice(0, 2), 0, "选择题", opts),
    "一、选择题（共 2 小题，每小题 5 分，共 10 分）"
  );
  assert.equal(examExport.questionScorePrefix(items[0], items.slice(0, 2), opts), "");
  assert.equal(examExport.questionScorePrefix(items[2], [items[2]], opts), "（15 分）");
  assert.equal(
    examExport.questionScorePrefix(items[0], items, { ...opts, group_mode: "basket_order" }),
    "（5 分）"
  );
});

test("ExamLayout.paginate builds A3 2-column sheets with mirrored odd/even seal bands", async () => {
  const env = { colPx: 684 };
  const fakeDoc = {
    fonts: { ready: Promise.resolve() },
    createElement(tag) {
      return makeFakeElement(tag, env, fakeDoc);
    },
    createTextNode(txt) {
      return { nodeType: 3, textContent: String(txt || "") };
    },
  };
  fakeDoc.body = makeFakeElement("body", env, fakeDoc);
  fakeDoc.defaultView = { getComputedStyle: () => ({ marginTop: "0", marginBottom: "8" }) };
  const prevGetComputedStyle = globalThis.getComputedStyle;
  globalThis.getComputedStyle = fakeDoc.defaultView.getComputedStyle;
  try {
    const source = makeFakeElement("div", env, fakeDoc);
    const title = makeFakeElement("h2", env, fakeDoc);
    title.className = "print-title";
    title._height = 40;
    source.append(title);
    for (let i = 0; i < 6; i++) {
      const q = makeFakeElement("div", env, fakeDoc);
      q.className = "print-question";
      q.dataset.questionId = `q${i + 1}`;
      q._height = 460;
      source.append(q);
    }
    const footer = makeFakeElement("p", env, fakeDoc);
    footer.className = "print-footer";
    footer._height = 24;
    source.append(footer);

    const host = makeFakeElement("article", env, fakeDoc);
    const result = await layout.paginate(source, {
      host,
      paper_spec: "a3_2col",
      seal_line: true,
      pagination: "compact",
    });
    assert.ok(result.page_count >= 2, `Expected >=2 A3 pages, got ${result.page_count}`);
    const pages = host.querySelectorAll(".exam-page");
    assert.equal(pages.length, result.page_count);
    assert.equal(pages[0].classList.contains("is-a3-2col"), true);
    assert.equal(pages[0].classList.contains("has-seal-left"), true);
    assert.equal(pages[0].classList.contains("has-seal-right"), false);
    assert.equal(pages[1].classList.contains("is-a3-2col"), true);
    assert.equal(pages[1].classList.contains("has-seal-right"), true);
    assert.equal(pages[1].classList.contains("has-seal-left"), false);
    const band0 = pages[0].querySelector(".exam-seal-band");
    const band1 = pages[1].querySelector(".exam-seal-band");
    assert.ok(band0 && band0.textContent.replace(/\s+/g, "").includes("密封线内不要答题"));
    assert.ok(band1 && band1.textContent.includes("学校"));
  } finally {
    if (prevGetComputedStyle === undefined) delete globalThis.getComputedStyle;
    else globalThis.getComputedStyle = prevGetComputedStyle;
  }
});

test("Eagle UI exposes Stage 4 paper-spec, seal-line, scoring, and usage-history controls", () => {
  const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");
  for (const id of [
    'id="excludeUsedSelect"',
    'id="usageHistoryModal"',
    'id="exportPaperSpec"',
    'id="exportSealLine"',
    'id="exportScoringEnabled"',
    'id="scoringConfigPanel"',
    'id="exportRecordUsage"',
    'id="exportTotalScoreBadge"',
  ]) {
    assert.ok(html.includes(id), `Missing control ${id} in eagle/index.html`);
  }
  const matches = html.match(/const usageSummary = /g) || [];
  assert.equal(matches.length, 1, "convertLibraryListItem should define usageSummary once");
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  for (const m of scripts) {
    if (/\bsrc\s*=/.test(m[1])) continue;
    new vm.Script(m[2]);
  }
});
