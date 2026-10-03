"use strict";

// Exercise the real crop/viewer image handlers without a browser or OCR service.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

class Element {
  constructor(tag, className = "", text = "") {
    this.tagName = tag.toUpperCase(); this.className = className; this.textContent = text;
    this.children = []; this.style = {}; this.dataset = {}; this.events = new Map();
    this.requests = []; this.hidden = false; this.complete = false; this.naturalWidth = 0;
    this.classList = {
      add: (name) => { const names = new Set(this.className.split(" ").filter(Boolean)); names.add(name); this.className = [...names].join(" "); },
      toggle: (name, show) => { const names = new Set(this.className.split(" ").filter(Boolean)); if (show) names.add(name); else names.delete(name); this.className = [...names].join(" "); }
    };
  }
  set src(value) { this._src = value; this.requests.push(value); this.complete = false; this.naturalWidth = 0; }
  get src() { return this._src; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { const handlers = this.events.get(name) || []; handlers.push(fn); this.events.set(name, handlers); }
  dispatch(name) {
    if (name === "load" || name === "error") { this.complete = true; this.naturalWidth = name === "load" ? this.width || 100 : 0; }
    for (const fn of this.events.get(name) || []) fn({ preventDefault() {}, stopPropagation() {} });
  }
  contains(node) { return this === node || this.children.some((child) => child.contains(node)); }
  querySelectorAll(selector) {
    const matches = (node) => selector.startsWith(".") ? node.className.split(" ").includes(selector.slice(1)) : node.tagName.toLowerCase() === selector;
    return this.children.flatMap((child) => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
}
const cropSource = js.slice(js.indexOf("  function cropView("), js.indexOf("  // ---------------------------------------------------------------- 放大镜"));
const context = {
  el: (tag, className, text) => new Element(tag, className, text),
  pageInfo: () => ({ width: 600, height: 800 }),
  previewUrl: (_id, page) => `/pages/${page}/preview.png`,
  state: { paperId: "isolated-test" }, PREVIEW_LONG_SIDE: 2000,
  spotBoxes: () => [], SLOT_NAMES: { stem: "题干" }
};
vm.runInNewContext(`${cropSource}\nthis.createCrop = cropView;`, context);
const regions = [{ page_idx: 1, bbox: [100, 200, 600, 450] }, { page_idx: 0, bbox: [50, 500, 450, 800] }];
const pieces = regions.map((region, index) => ({ ...region, bbox: [...region.bbox], order: index, width: index ? 320 : 400, height: index ? 240 : 200, url: `/saved/${index}.png` }));
const originalInput = JSON.stringify({ regions, pieces });

function makeCrop(saved = pieces, source = regions, extras = {}) {
  return context.createCrop(source, { fallbackImages: saved, ...extras });
}
function viewerTracking(wrap) {
  const crop = new Element("div"); crop.append(wrap);
  const status = new Element("p"); const dialog = { open: true };
  const viewer = { mode: "fit", imageReady: false }; const calls = { zoom: 0, fit: 0, stop: 0 };
  const renderStart = js.indexOf("  function renderViewer()");
  const start = js.indexOf('    const images = [...crop.querySelectorAll("img")];', renderStart);
  const end = js.indexOf("    // 又宽又矮的截图", start);
  assert.ok(start > renderStart && end > start, "Use the real viewer image state block");
  vm.runInNewContext(js.slice(start, end), {
    crop, viewer, $: (id) => id === "viewerDialog" ? dialog : status,
    syncViewerZoom: () => calls.zoom++, requestViewerFit: () => calls.fit++, stopViewerSourcePan: () => calls.stop++
  });
  return { viewer, status, calls };
}

// Saved pieces follow the region array, including deliberately reversed pages.
const crop = makeCrop(pieces, regions, { onZoom: () => {} });
const images = crop.querySelectorAll("img");
assert.deepEqual(images.map((image) => image.src), ["/pages/1/preview.png", "/pages/0/preview.png"]);
assert.match(crop.className, /zoomable/);
images[0].dispatch("error");
assert.equal(images[0].src, "/saved/0.png");
assert.equal(images[0].style.width, "100%");
assert.equal(images[0].style.left, "0"); assert.equal(images[0].style.top, "0");
assert.equal(images[0].width, 400); assert.equal(images[0].height, 200);
assert.equal(crop.querySelectorAll(".crop-seg")[0].style.aspectRatio, "400 / 200");
assert.equal(images[0].dataset.cropFallback, "loading");
images[0].dispatch("load");
assert.equal(images[0].dataset.cropFallback, "ready");
assert.equal(crop.querySelectorAll(".hint")[0].hidden, true);
images[1].dispatch("error");
assert.equal(images[1].src, "/saved/1.png");
assert.equal(JSON.stringify({ regions, pieces }), originalInput, "Fallback must never edit source ranges or saved evidence");

// If the fallback also fails, report recovery once rather than retrying forever.
images[1].dispatch("error"); images[1].dispatch("error");
assert.deepEqual(images[1].requests, ["/pages/0/preview.png", "/saved/1.png"]);
assert.equal(images[1].dataset.cropFallback, "failed");
assert.equal(crop.querySelectorAll(".hint")[1].hidden, false);
assert.match(crop.querySelectorAll(".hint")[1].textContent, /调整范围/);

// Missing, stale or reordered crops must not replace a different source region.
for (const saved of [[], [pieces[1], pieces[0]], [{ ...pieces[0], bbox: [100, 200, 600, 451] }], [{ ...pieces[0], bbox: undefined }]]) {
  const node = makeCrop(saved); const image = node.querySelectorAll("img")[0];
  image.dispatch("error");
  assert.deepEqual(image.requests, ["/pages/1/preview.png"]);
  assert.equal(node.querySelectorAll(".hint")[0].hidden, false);
}
const textCrop = context.createCrop([regions[0]]);
textCrop.querySelectorAll("img")[0].dispatch("error");
assert.equal(textCrop.querySelectorAll("img")[0].dataset.cropSource, undefined, "Text questions cannot pick up source-image fallbacks");
assert.equal(textCrop.querySelectorAll(".hint")[0].hidden, false);

// The viewer must not count the first failed URL as final failure while the
// saved crop is loading. Once loaded it enables existing zoom/pan/fit controls.
const viewerCrop = makeCrop([pieces[0]], [regions[0]]);
const tracking = viewerTracking(viewerCrop); const viewerImage = viewerCrop.querySelectorAll("img")[0];
assert.equal(tracking.viewer.imageReady, false);
viewerImage.dispatch("error");
assert.equal(tracking.viewer.imageReady, false);
assert.doesNotMatch(tracking.status.className, /image-error/);
assert.match(tracking.status.textContent, /已保存的题目裁片/);
viewerImage.dispatch("load");
assert.equal(tracking.viewer.imageReady, true);
assert.equal(tracking.status.hidden, true);
assert.ok(tracking.calls.fit > 0 && tracking.calls.zoom > 0);

const failedViewerCrop = makeCrop([pieces[0]], [regions[0]]);
const failedTracking = viewerTracking(failedViewerCrop); const failedImage = failedViewerCrop.querySelectorAll("img")[0];
failedImage.dispatch("error"); failedImage.dispatch("error");
assert.equal(failedTracking.viewer.imageReady, false);
assert.match(failedTracking.status.className, /image-error/);
assert.match(failedTracking.status.textContent, /调整范围后保存/);
assert.ok(failedTracking.calls.stop > 0);

// Multi-piece questions remain usable after one image loads and keep the
// pending/error notice for the other piece instead of hiding it prematurely.
const multiCrop = makeCrop(); const multi = viewerTracking(multiCrop); const multiImages = multiCrop.querySelectorAll("img");
multiImages[0].dispatch("load");
assert.equal(multi.viewer.imageReady, true); assert.equal(multi.status.hidden, false);
multiImages[1].dispatch("error"); multiImages[1].dispatch("error");
assert.equal(multi.viewer.imageReady, true);
assert.equal(multi.status.hidden, false); assert.match(multi.status.textContent, /部分题目图片/);

const viewerCall = js.slice(js.indexOf("  function renderViewer()"), js.indexOf("    const images =", js.indexOf("  function renderViewer()")));
assert.match(viewerCall, /fallbackImages: q\.body_mode === "source_image" \? q\.question_images \|\| \[\] : \[\]/);
assert.match(js, /sticky\.append\(cropView\(q\.regions, \{[^\n]+fallbackImages: q\.body_mode === "source_image"/);
console.log("Source-image preview fallback: saved range/order, one retry, recovery notices and viewer zoom/pan readiness OK");
