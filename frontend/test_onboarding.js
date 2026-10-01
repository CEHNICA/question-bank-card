"use strict";

// First launch explains the three steps and can walk through the screen; the
// title carries no engine details.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// Nothing technical under the title; it only speaks up when reading cannot work.
assert.match(html, /<span id="engineLine" class="engine-line" hidden><\/span>/);
assert.doesNotMatch(js, /旁证 MinerU 文字|有出入时复核 \$\{s\.checker\}/);
assert.match(js, /brandNotice\(s\.upload_enabled \? "" : "还不能读新资料：点右上角“设置”填写密钥", "warn"\)/);
assert.match(js, /brandNotice\("连不上本机服务：请关掉题有据再重新打开", "error"\)/);

// Welcome: three steps, the service check, and the way into the tour; shown once.
assert.match(html, /<dialog id="welcomeDialog"[\s\S]*?<strong>上传<\/strong>[\s\S]*?<strong>核对<\/strong>[\s\S]*?<strong>入库<\/strong>/);
assert.match(html, /id="welcomeKeys"[^>]*hidden>先去填写密钥<\/button>/);
assert.match(html, /id="welcomeTour"[^>]*>只看一遍界面<\/button>/);
assert.match(js, /if \(readPref\("qb-welcome-seen", ""\) !== "1"\) openWelcome\(\);/);
assert.match(js, /writePref\("qb-welcome-seen", "1"\)/);
assert.match(html, /id="settingsWelcome"[^>]*>重新看一遍新手引导<\/button>/);

// Tour: ten stops, missing ones skipped, keys owned by the tour while it is open.
const steps = js.match(/const TOUR_STEPS = \[([\s\S]*?)\n  \];/)[1];
for (const title of ["上传资料", "试卷列表", "题卡", "对了就打勾", "不对就改", "先看有疑点的", "专注和全屏", "入库", "正式题库", "设置"]) {
  assert.match(steps, new RegExp(`title: "${title}"`));
}
assert.match(js, /tour\.steps = TOUR_STEPS\.filter\(\(step\) => tourVisible\(step\.target\(\)\)\);/);
assert.match(js, /document\.addEventListener\("keydown", \(event\) => \{\s*if \(\$\("tour"\)\.hidden\) return;[\s\S]*?\}, true\);/);
assert.match(css, /\.tour-spot \{[^}]*box-shadow: 0 0 0 9999px/);

console.log("onboarding checks: OK");
