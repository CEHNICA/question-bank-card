"use strict";
const assert = require("node:assert/strict");
const QB = require("./qb-render.js");

class Element {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase(); this.ownerDocument = doc; this.children = [];
    this.className = ""; this.dataset = {}; this.events = new Map(); this.textContent = "";
    this.classList = {
      add: (name) => { if (!this.className.split(" ").includes(name)) this.className += ` ${name}`; },
      toggle: (name, show) => { const set = new Set(this.className.split(" ").filter(Boolean)); if (show) set.add(name); else set.delete(name); this.className = [...set].join(" "); }
    };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(name, callback) { this.events.set(name, callback); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const matches = (node) => selector.startsWith(".") ? node.className.split(" ").includes(selector.slice(1)) : node.tagName.toLowerCase() === selector;
    return this.children.flatMap((child) => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
}
const doc = { createElement: (tag) => new Element(tag, doc) };
const host = doc.createElement("div");
const content = {
  number: 7, body_mode: "source_image", stem: "旧识读不可混入原图", options: { A: "旧选项" },
  figures: [{ slot: "stem", url: "/old-figure.png" }],
  question_images: [
    { url: "/page-2-piece.png", page_idx: 1, order: 1, width: 900, height: 200 },
    { url: "/page-1-piece.png", page_idx: 0, order: 0, width: 500, height: 300 }
  ]
};
QB.renderQuestion(host, content, { showAnswer: "none" });
assert.equal(host.querySelector(".qb-number").textContent, "7.");
assert.deepEqual(host.querySelectorAll("img").map((image) => image.src), ["/page-2-piece.png", "/page-1-piece.png"], "Saved array order must survive cross-page pieces");
assert.equal(host.querySelectorAll(".qb-figure").length, 0, "Figures in the source crop must not be duplicated");
assert.equal(host.querySelectorAll(".qb-options").length, 0, "No empty options or stale OCR may be rendered");
assert.equal(host.querySelector(".qb-stem-body"), null);
assert.equal(host.querySelectorAll("img")[0].width, 900);
assert.match(host.querySelectorAll("img")[0].alt, /原卷第 2 页/);
const firstImage = host.querySelectorAll("img")[0];
firstImage.events.get("error")(); firstImage.events.get("error")();
assert.equal(host.querySelectorAll(".qb-image-error").length, 1, "A failed crop must show one actionable warning");

QB.renderQuestion(host, content, { showNumber: false, showAnswer: "none", resolveQuestionImageUrl: (_piece, index) => `/api/crop/${index}` });
assert.equal(host.querySelector(".qb-number"), null);
assert.deepEqual(host.querySelectorAll("img").map((image) => image.src), ["/api/crop/0", "/api/crop/1"]);
QB.renderQuestion(host, { body_mode: "source_image" }, { showNumber: false, showAnswer: "none" });
assert.match(host.querySelector(".qb-image-error").textContent, /调整范围后保存/);
assert.equal(host.querySelectorAll("img").length, 0);
console.log("Source-image body, ordering, duplicate suppression and missing crop checks: OK");
