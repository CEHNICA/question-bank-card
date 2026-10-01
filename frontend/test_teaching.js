"use strict";

// 新手教学: a practice paper and lessons that finish when the user does the step.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { TEACH_LESSONS, lessonDone, lessonHint } = require("./app.js");

assert.deepEqual(TEACH_LESSONS.map((lesson) => lesson.key),
  ["card", "viewer", "tick", "todo", "fix", "tick9", "figure", "table", "green", "publish", "finish"]);
assert.ok(TEACH_LESSONS[0].manual && TEACH_LESSONS.at(-1).final);

// Each step finishes on the matching action, and only then.
assert.ok(lessonDone("viewer", { type: "viewer", number: 4 }));
assert.ok(lessonDone("tick", { type: "approve", number: 1 }));
assert.ok(lessonDone("todo", { type: "filter", key: "todo" }));
assert.ok(!lessonDone("todo", { type: "filter", key: "all" }));
assert.ok(lessonDone("fix", { type: "text", number: 9, stem: "将点 P 向右移动 5 个单位后表示（ ）" }));
assert.ok(lessonDone("fix", { type: "text", number: 9, stem: "向右移动5个单位" }));
assert.ok(!lessonDone("fix", { type: "text", number: 9, stem: "向右移动 4 个单位" }));
assert.match(lessonHint("fix", { type: "text", number: 9, stem: "向右移动 4 个单位" }), /原卷印的是“向右移动 5 个单位”/);
assert.ok(lessonDone("tick9", { type: "approve", number: 9 }));
assert.ok(!lessonDone("tick9", { type: "approve", number: 1 }));
assert.ok(lessonDone("figure", { type: "figures", number: 2, figures: 1 }));
assert.ok(!lessonDone("figure", { type: "figures", number: 2, figures: 0 }));
assert.ok(lessonDone("table", { type: "approve", number: 3 }));
assert.ok(lessonDone("green", { type: "approveGreen" }));
assert.ok(lessonDone("publish", { type: "publish" }));
assert.ok(!lessonDone("card", { type: "approve" }), "a reading step moves on only with 下一步");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
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
assert.match(js, /if \(state\.paper\?\.demo\) \{\s*teach\(\{ type: "publish" \}\);\s*await confirmDialog\(\{\s*title: "示例试卷不会入库"/);
// “指给我看” does not block the page, and Esc still closes an open window first.
assert.match(js, /if \(event\.key === "Escape" && !anyDialogOpen\(\)\) \{ endTour\(\);/);
assert.match(js, /clearTimeout\(tour\.pending\);/);

console.log("teaching checks: OK");
