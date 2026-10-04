"use strict";

// First launch explains the three steps and can walk through the screen; the
// title carries no engine details.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// Missing cloud credentials must not block the local workflow.
assert.match(html, /<span id="engineLine" class="engine-line" hidden><\/span>/);
assert.doesNotMatch(js, /旁证 MinerU 文字|有出入时复核 \$\{s\.checker\}/);
assert.match(js, /brandNotice\(""\)/);
assert.doesNotMatch(js, /还不能读新资料：点右上角“设置”填写密钥/);
assert.doesNotMatch(html, /id="parseMode"/);
assert.match(html, /上传原卷，程序先尝试切题/);
assert.match(js, /brandNotice\("连不上本机服务：请关掉题有据再重新打开", "error"\)/);

// Automatic flow is explained separately from an actual offline manual course.
assert.match(html, /<dialog id="welcomeDialog"[\s\S]*?<strong>上传<\/strong>[\s\S]*?<strong>核对<\/strong>[\s\S]*?<strong>入库<\/strong>/);
assert.doesNotMatch(html, /id="welcomeKeys"|配置自动切题（可稍后）/,
  "first launch keeps API setup in settings instead of adding a fourth competing action");
assert.match(html, /id="welcomeLearn"[^>]*>开始手工练习<\/button>/);
assert.match(html, /id="welcomeTour"[^>]*>自动切题入门<\/button>/);
assert.match(js, /requestAnimationFrame\(\(\) => \$\("welcomeTour"\)\.focus\(\)\)/,
  "keyboard focus starts on the primary automatic-cutting guide");
assert.match(js, /以后需要云处理或主动识读时，再到设置中的 API 配置填写密钥/);
assert.match(js, /if \(readPref\("qb-welcome-seen", ""\) !== "1"\) openWelcome\(\);/);
assert.match(js, /writePref\("qb-welcome-seen", "1"\)/);
const steps = js.match(/const TOUR_STEPS = \[([\s\S]*?)\n  \];/)[1];
for (const title of ["上传资料", "继续已有试卷", "核对后通过，自动入库", "找题和组卷", "遇到问题时看帮助"]) {
  assert.ok(steps.includes(`title: "${title}"`));
}
assert.equal((steps.match(/title:/g) || []).length, 5, "screen tour no longer repeats the approval/bank entry");
assert.doesNotMatch(steps, /title: "入库"|只看新版功能/);
assert.match(js, /tour\.steps = TOUR_STEPS\.filter\(\(step\) => tourVisible\(step\.target\(\)\)\);/);
assert.match(js, /document\.addEventListener\("keydown", \(event\) => \{\s*if \(\$\("tour"\)\.hidden\) return;[\s\S]*?\}, true\);/);
assert.match(js, /if \(\$\("tour"\)\.hidden\) return;[\s\S]*?if \(anyDialogOpen\(\)\) return;/,
  "optional tour cannot consume modal save shortcuts");
assert.match(css, /\.teach-panel \{ position: static;/,
  "the practical guide reserves layout space instead of covering questions");
assert.match(css, /\.tour-spot \{[^}]*box-shadow: 0 0 0 9999px/);

console.log("onboarding checks: OK");
