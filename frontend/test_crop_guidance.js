"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
const css = fs.readFileSync(require.resolve("./styles.css"), "utf8");
const prefStart = source.indexOf('  const CROP_GUIDANCE_PREF =');
const prefEnd = source.indexOf("  function cropSnapshot()", prefStart);
const start = source.indexOf("  function showCropResult(");
const end = source.indexOf("  function configureCropActions()", start);
const prefs = new Map();
function harness() {
  const nodes = new Map(), listeners = new Map(), writes = [];
  const $ = (id) => {
    if (id === "pageStage") throw Error("Changing a hint must not touch canvas geometry, scrolling or zoom");
    if (!nodes.has(id)) nodes.set(id, { id, hidden: true, events: {}, classes: new Set(),
      classList: { toggle(name, on) { const value = nodes.get(id); if (on) value.classes.add(name); else value.classes.delete(name); } },
      addEventListener(name, handler) { this.events[name] = handler; } });
    return nodes.get(id);
  };
  const context = { $, readPref: (key, fallback) => prefs.get(key) ?? fallback,
    writePref: (key, value) => { prefs.set(key, value); writes.push([key, value]); },
    window: { addEventListener: (name, handler) => listeners.set(name, handler) } };
  vm.runInNewContext(source.slice(prefStart, prefEnd) + source.slice(start, end), context);
  return { context, $, writes, listeners };
}

const initial = harness();
initial.context.showCropGuide("移动鼠标到另一角，再点一下固定范围");
assert.equal(initial.$("pageCropGuide").hidden, false);
initial.context.showCropResult("第7题已保存", false);
initial.$("pageGuidanceToggle").events.change({ target: { checked: false } });
assert.deepEqual(initial.writes, [["qb-crop-guidance", "0"]]);
assert.equal(initial.$("pageCropGuide").hidden, true);
assert.equal(initial.$("pageGuidanceToggle").checked, false);
assert.equal(initial.$("pageCropResult").hidden, false, "Opting out of guidance never hides save feedback");
initial.context.showCropResult("范围太小，请调整后重试", true);
assert.equal(initial.$("pageCropResult").hidden, false);
assert.equal(initial.$("pageCropResult").classes.has("error"), true, "Required errors remain visible when guidance is disabled");
initial.context.showCropGuide("第二个框的操作指导");
assert.equal(initial.$("pageCropGuide").hidden, true);
const reopened = harness(); reopened.context.showCropGuide("重新打开窗口的指导");
assert.equal(reopened.$("pageCropGuide").hidden, true, "The choice persists when the software opens the next crop window");
reopened.$("pageGuidanceToggle").events.change({ target: { checked: true } });
assert.equal(reopened.$("pageCropGuide").hidden, false, "The shortcut help switch restores guidance without reinstalling or resetting other preferences");
assert.deepEqual(reopened.writes, [["qb-crop-guidance", "1"]]);
initial.listeners.get("storage")({ key: "qb-crop-guidance", newValue: "1" });
assert.equal(initial.$("pageCropGuide").hidden, false, "Another crop page's preference change updates this open window");
initial.listeners.get("storage")({ key: "other-pref", newValue: "0" });
assert.equal(initial.$("pageCropGuide").hidden, false);
initial.context.showCropGuide("");
assert.equal(initial.$("pageCropGuide").hidden, true);

assert.match(html, /class="page-crop-workspace">\s*<div class="page-crop-messages">[\s\S]*?id="pageCropResult"[\s\S]*?id="pageCropGuide"[\s\S]*?id="pageStage"/);
assert.match(css, /\.page-crop-workspace\s*\{[^}]*position:\s*relative/);
assert.match(css, /\.page-crop-messages\s*\{[^}]*position:\s*absolute[^}]*pointer-events:\s*none/);
// 提示条是浮在原卷上的，里面不能再放能点的东西。窄屏时「以后不再提示」正好压在
// 用户正要点的第二个角上，点下去框没收成，只把提示关了。同一个开关在「快捷键」面板里。
assert.match(html, /id="pageCropGuide"[^>]*>\s*<span id="pageCropGuideText"><\/span>\s*<\/div>/);
assert.doesNotMatch(css, /page-crop-(?:messages|guide)[^{]*\{[^}]*pointer-events:\s*auto/);
assert.match(html, /id="pageGuidanceToggle"/);

console.log("Crop guidance: out-of-flow overlay, geometry-independent DOM updates, persistent opt-out, recovery switch and always-visible necessary feedback: OK");
