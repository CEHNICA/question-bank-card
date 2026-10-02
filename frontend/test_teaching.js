"use strict";

// 新手教学: a practice paper and lessons that finish when the user does the step.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { TEACH_LESSONS, lessonDone, lessonHint, restoreTeaching, teachingProgress } = require("./app.js");

assert.deepEqual(TEACH_LESSONS.map((lesson) => lesson.key),
  ["card", "viewer", "tick", "fix", "figure", "publish", "basics", "original", "preview", "region", "library", "recovery", "finish"]);
assert.ok(TEACH_LESSONS[0].manual && TEACH_LESSONS.at(-1).final);
assert.equal(TEACH_LESSONS.filter((lesson) => lesson.section === "basic").length, 7);
assert.equal(TEACH_LESSONS.filter((lesson) => lesson.section === "review" && !lesson.final).length, 5);
assert.ok(TEACH_LESSONS.find((lesson) => lesson.key === "basics").checkpoint);

// Stable keys and the old course's indexes must refer to learning content,
// including the old finished screen; invalid progress starts safely at card.
const oldKeys = ["card", "viewer", "tick", "fix", "fix", "figure", "figure", "publish", "publish", "publish", "basics"];
oldKeys.forEach((key, index) => {
  const restored = restoreTeaching({ paper: "demo", index });
  assert.equal(TEACH_LESSONS[restored.index].key, key);
  assert.equal(restored.migrated, true);
});
for (const lesson of TEACH_LESSONS) {
  const restored = restoreTeaching({ paper: "demo", version: 2, lesson: lesson.key, index: -999 });
  assert.equal(TEACH_LESSONS[restored.index].key, lesson.key);
  assert.equal(restored.migrated, false);
}
for (const index of [-1, 11, 999, 1.5, "4", NaN, Infinity, undefined]) {
  assert.equal(restoreTeaching({ paper: "demo", index }).index, 0);
}
for (const saved of [null, [], "demo", {}, { paper: 4 }, { paper: "" }]) assert.equal(restoreTeaching(saved), null);
assert.equal(restoreTeaching({ paper: "demo", version: 2, lesson: "removed" }).index, 0);
assert.equal(restoreTeaching({ paper: "demo", version: 2, lesson: "fix", completed: true }).completed, "fix",
  "a completed action can resume without forcing another save after a leave warning");
assert.equal(restoreTeaching({ paper: "demo", version: 2, lesson: "viewer", completed: true }).completed, null);
assert.equal(restoreTeaching({ paper: "demo", index: 4, completed: true }).completed, null);
assert.deepEqual(teachingProgress(-1), teachingProgress(0));
for (let index = 0; index < TEACH_LESSONS.length; index += 1) {
  const progress = teachingProgress(index);
  assert.ok(progress.current >= 1 && progress.current <= progress.total);
  assert.equal(progress.lesson.key, TEACH_LESSONS[index].key);
}
assert.equal(teachingProgress(7).current, 1);
assert.equal(teachingProgress(7).total, 5);

// Each step finishes on the matching action, and only then.
assert.ok(!lessonDone("viewer", { type: "viewer", number: 1 }), "opening a viewer alone must not skip the gesture explanation");
assert.ok(lessonDone("tick", { type: "approve", number: 1 }));
assert.ok(!lessonDone("tick", { type: "approve", number: 2 }));
assert.ok(lessonDone("fix", { type: "text", number: 9, stem: "将点 P 向右移动 5 个单位后表示（ ）" }));
assert.ok(lessonDone("fix", { type: "text", number: 9, stem: "向右移动5个单位" }));
assert.ok(!lessonDone("fix", { type: "text", number: 9, stem: "向右移动 4 个单位" }));
assert.match(lessonHint("fix", { type: "text", number: 9, stem: "向右移动 4 个单位" }), /原卷印的是“向右移动 5 个单位”/);
assert.ok(lessonDone("figure", { type: "figures", number: 2, figures: 1 }));
assert.ok(!lessonDone("figure", { type: "figures", number: 2, figures: 0 }));
assert.ok(lessonDone("publish", { type: "publish" }));
assert.ok(!lessonDone("card", { type: "approve" }), "a reading step moves on only with 下一步");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
// Entry points: welcome, empty page, settings → 帮助.
assert.match(html, /id="welcomeLearn"[^>]*>用示例试卷学一遍<\/button>/);
assert.match(html, /id="emptyLearn"[^>]*>先用示例试卷学一遍（不用密钥）<\/button>/);
assert.match(html, /id="settingsLearn"[^>]*>用示例试卷重新学一遍<\/button>/);
assert.match(js, /api\("\/api\/demo", \{ method: "POST", body: \{ reset \} \}\)/);
// The review actions report to the lesson.
for (const event of ['type: "viewer"', 'type: "approve"', 'type: "filter"', 'type: "text"', 'type: "figures"', 'type: "approveGreen"', 'type: "publish"']) {
  assert.ok(js.includes(`teach({ ${event}`), event);
}
// The practice paper never publishes: 入库 explains instead.
assert.match(js, /if \(state\.paper\?\.demo\) \{\s*await confirmDialog\(\{\s*title: "示例试卷不会入库"[\s\S]*?\}\);\s*teach\(\{ type: "publish" \}\);\s*return;/);
assert.match(js, /if \(dialog\.mode === "view" \|\| dialog\.practiceRead\) return;/);
assert.match(html, /id="settingsNewFeatures"/);
// “指给我看” does not block the page, and Esc still closes an open window first.
assert.match(js, /if \(event\.key === "Escape" && !anyDialogOpen\(\)\) \{ endTour\(\);/);
assert.match(js, /clearTimeout\(tour\.pending\);/);

// The lesson card and the pointer stay on top: an open window (zoom viewer,
// figure picker, confirm box) sits in the browser's top layer, so they move
// into the window opened last and come back when it closes.
assert.match(js, /const host = openDialogs\[openDialogs\.length - 1\] \|\| document\.body;/);
assert.match(js, /\[\$\("tour"\), \$\("teachPanel"\)\]\.forEach\(\(node\) => \{ if \(node\.parentNode !== host\) host\.append\(node\); \}\);/);
assert.match(js, /new MutationObserver\(liftGuides\)\.observe\(document\.body, \{ subtree: true, attributes: true, attributeFilter: \["open"\] \}\);/);
assert.match(css, /dialog\[open\]:has\(> \.teach-panel:not\(\[hidden\]\), > \.tour:not\(\[hidden\]\)\) \{ animation-name: dialog-fade; \}/);
assert.match(css, /\.viewer-dialog > \.teach-panel \{ left: auto; right: 20px; bottom: 78px; \}/);

console.log("teaching checks: OK");
