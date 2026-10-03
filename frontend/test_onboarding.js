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

// Welcome: three steps, the service check, and the way into the tour; shown once.
assert.match(html, /<dialog id="welcomeDialog"[\s\S]*?<strong>上传<\/strong>[\s\S]*?<strong>核对<\/strong>[\s\S]*?<strong>入库<\/strong>/);
assert.match(html, /id="welcomeKeys"[^>]*hidden>配置自动切题（可稍后）<\/button>/);
assert.match(html, /id="welcomeTour"[^>]*>只看一遍界面<\/button>/);
assert.match(js, /if \(readPref\("qb-welcome-seen", ""\) !== "1"\) openWelcome\(\);/);
assert.match(js, /writePref\("qb-welcome-seen", "1"\)/);
assert.match(html, /id="settingsWelcome"[^>]*>重新看一遍新手引导<\/button>/);

// Tour: current workflow, missing stops skipped, keys owned while it is open.
const steps = js.match(/const TOUR_STEPS = \[([\s\S]*?)\n  \];/)[1];
for (const title of ["上传资料", "试卷列表", "题卡", "对了就打勾", "不对就改", "看整份原卷", "先看有疑点的", "专注和全屏", "入库", "正式题库", "设置"]) {
  assert.match(steps, new RegExp(`title: "${title}"`));
}
assert.match(steps, /范围不对点“调整范围”，只保存范围/);
assert.match(steps, /更多 → 框选识读（纠错）/);
assert.doesNotMatch(steps, /点“框选识读”|上传.*MinerU.*(?:必需|必须)/);
assert.match(js, /tour\.steps = TOUR_STEPS\.filter\(\(step\) => tourVisible\(step\.target\(\)\)\);/);
assert.match(js, /document\.addEventListener\("keydown", \(event\) => \{\s*if \(\$\("tour"\)\.hidden\) return;[\s\S]*?\}, true\);/);
assert.match(css, /\.tour-spot \{[^}]*box-shadow: 0 0 0 9999px/);

console.log("onboarding checks: OK");
