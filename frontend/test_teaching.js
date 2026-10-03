"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { TEACH_LESSONS, lessonDone, lessonHint, restoreTeaching, teachingProgress } = require("./app.js");

assert.deepEqual(TEACH_LESSONS.filter((lesson) => lesson.section === "basic").map((lesson) => lesson.key),
  ["cut", "cutComplete", "fix", "tick9", "tick", "library", "basket", "export", "finish"]);
assert.deepEqual(TEACH_LESSONS.filter((lesson) => lesson.section !== "basic").map((lesson) => lesson.key), ["figure"]);
for (const key of ["cut", "cutComplete", "fix", "tick9", "tick", "library", "basket", "export"]) {
  assert.ok(!TEACH_LESSONS.find((lesson) => lesson.key === key).manual,
    `${key} requires an actual action instead of advancing a description`);
}
assert.ok(lessonDone("cut", { type: "cut", number: 1 }));
assert.ok(!lessonDone("cut", { type: "cut", number: 2 }));
assert.ok(!lessonDone("cut", { type: "viewer", number: 1 }));
assert.ok(lessonDone("cutComplete", { type: "cutComplete" }));
assert.ok(!lessonDone("cutComplete", { type: "cut", number: 1 }));
assert.ok(lessonDone("fix", { type: "text", number: 9, stem: "将点 P 向右移动 5 个单位" }));
assert.ok(!lessonDone("fix", { type: "text", number: 9, stem: "向右移动 4 个单位" }));
assert.ok(!lessonDone("fix", { type: "text", number: 1, stem: "向右移动 5 个单位" }));
assert.match(lessonHint("fix", { type: "text", number: 9 }), /5 个单位/);
assert.ok(lessonDone("tick9", { type: "approve", number: 9 }));
assert.ok(!lessonDone("tick9", { type: "approve", number: 1 }));
assert.ok(lessonDone("tick", { type: "approve", number: 1 }));
assert.ok(!lessonDone("tick", { type: "approve", number: 9 }));
assert.ok(lessonDone("figure", { type: "figures", number: 2, figures: 1 }));
assert.ok(!lessonDone("figure", { type: "figures", number: 2, figures: 0 }));
for (const key of ["library", "basket", "export"]) {
  for (const type of ["approve", "publish", "viewer", "test"]) assert.ok(!lessonDone(key, { type, number: 1 }),
    "the review guide cannot invent a completed bank/export action");
}
for (const lesson of TEACH_LESSONS) {
  const restored = restoreTeaching({ paper: "demo", version: 3, lesson: lesson.key, completed: true });
  assert.equal(TEACH_LESSONS[restored.index].key, lesson.key);
  assert.equal(restored.completed, lesson.key);
  assert.equal(restored.migrated, false);
  const progress = teachingProgress(restored.index);
  assert.ok(progress.current >= 1 && progress.current <= progress.total);
}
for (const legacy of [{ paper: "demo", version: 2, lesson: "library", completed: true }, { paper: "demo", index: 4 }]) {
  const restored = restoreTeaching(legacy);
  assert.equal(TEACH_LESSONS[restored.index].key, "cut");
  assert.equal(restored.completed, null, "legacy explanations do not complete the new practical course");
  assert.equal(restored.migrated, true);
}
assert.equal(restoreTeaching({ paper: "demo", version: 3, lesson: "fix", active: false }).active, false,
  "closing the guide leaves resumable progress without showing it again on refresh");
for (const saved of [null, {}, "demo", [], { paper: 4 }, { paper: "" }]) assert.equal(restoreTeaching(saved), null);
assert.deepEqual(teachingProgress(-1), teachingProgress(0));
assert.equal(teachingProgress(9).total, 1, "advanced practice is a separate short task");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
assert.match(html, /id="settingsLearn"[^>]*>开始手工练习/);
assert.match(html, /id="settingsAutomaticGuide"[^>]*>自动切题入门/);
assert.match(html, /手工示例没有经过 MinerU 或 AI 处理/);
assert.match(html, /明确允许本次云处理/);
assert.match(html, /id="settingsHelpFaq"/);
assert.match(html, /id="settingsHelpKeys"/);
assert.doesNotMatch(html, /只看新版功能|用示例试卷重新学一遍|重新看一遍新手引导/);
assert.match(html, /<details class="teaching-help"><summary>题卡上的标记说明/);
assert.match(html, /<summary>重新练习与提示设置/);
assert.match(js, /body: \{ reset, course: "basics" \}/);
assert.match(js, /if \(data\.restart_required\)/);
assert.match(js, /await leaveFor\(teaching\.practiceUrl \|\| `\/practice\/\$\{teaching\.paper\}`\)/);
assert.match(js, /lesson\?\.section === "basic" \? TEACH_KEY : `\$\{TEACH_KEY\}-task`/,
  "advanced practice must not overwrite basic-course progress");
assert.match(js, /if \(state\.paper\?\.demo\) \{\s*focusCutReview\(\);\s*teach\(\{ type: "cutComplete" \}\)/,
  "finishing a demo goes directly to image review without cloud recognition");
assert.match(js, /teach\(\{ type: "cut", number \}\)/);
assert.match(js, /\$\("teachSkip"\)\.hidden = true/,
  "a skipped action cannot masquerade as a completed exercise");
assert.match(js, /if \(dialog\.mode === "view" \|\| dialog\.practiceRead\) return;/);
assert.match(js, /const host = openDialogs\[openDialogs\.length - 1\] \|\| document\.body;/);
assert.match(js, /mount = \$\("pageTeachMount"\)/);
assert.match(js, /mount = \$\("viewerTeachMount"\)/);
assert.match(js, /node\.classList\.add\("teaching-target"\)/);
assert.match(js, /await leaveFor\("\/#dropZone"\)/);
assert.match(js, /finally \{[\s\S]*?const resumedUrl = new URL\(window\.location\.href\);\s*resumedUrl\.searchParams\.delete\("learn"\);\s*window\.history\.replaceState\(null, "", resumedUrl\);/,
  "opening/reset requests are consumed even on cancellation while retaining paper and unrelated URL parameters");
console.log("teaching checks: OK");
