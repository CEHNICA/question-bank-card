const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const FRONTEND = path.resolve(__dirname);

test("Eagle workspace renders a floating update popup with one-click install and highlights", () => {
  const html = fs.readFileSync(path.join(FRONTEND, "eagle", "index.html"), "utf8");
  for (const id of [
    "eagleUpdatePopup",
    "eagleUpdatePopupTitle",
    "eagleUpdatePopupSub",
    "eagleUpdatePopupHighlights",
    "eagleUpdatePopupStatus",
    "eagleUpdatePopupProgress",
    "eagleUpdatePopupProgressStage",
    "eagleUpdatePopupProgressPct",
    "eagleUpdatePopupProgressFill",
    "eagleUpdatePopupProgressBytes",
    "eagleUpdatePopupProgressSpeed",
    "eagleUpdatePopupInstall",
    "eagleUpdatePopupLater",
    "eagleUpdatePopupClose",
    "eagleUpdatePopupAbout",
    "eaglePrefUpdateProgress",
    "eaglePrefUpdateProgressFill",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `Missing #${id} in eagle/index.html`);
  }
  assert.match(html, /function showEagleUpdatePopup\(data\)/);
  assert.match(html, /function applyOfficialUpdateState\(data/);
  assert.match(html, /function syncEagleUpdateProgressUI\(progress\)/);
  assert.match(html, /href="https:\/\/tiyouju\.com\/"/);
  assert.match(html, /\/api\/update\/install\/start/);
  assert.match(html, /\/api\/update\/progress/);
  assert.match(html, /\/api\/update\/install/);
  assert.match(html, /\/api\/update`/);
  assert.match(html, /\/api\/update\/settings/);
  assert.doesNotMatch(html, /eaglePrefAutoUpdate[\s\S]{0,600}api\/settings\/features/);
});

test("Main workspace & settings page render a matching floating update popup", () => {
  const html = fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8");
  const js = fs.readFileSync(path.join(FRONTEND, "app.js"), "utf8");
  const css = fs.readFileSync(path.join(FRONTEND, "styles.css"), "utf8");
  for (const id of [
    "updateFloatCard",
    "updateFloatTitle",
    "updateFloatSub",
    "updateFloatHighlights",
    "updateFloatStatus",
    "updateFloatProgress",
    "updateFloatProgressFill",
    "updateFloatInstall",
    "updateFloatLater",
    "updateFloatClose",
    "updateFloatAbout",
    "updateAboutProgress",
    "updateAboutProgressFill",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `Missing #${id} in frontend/index.html`);
  }
  assert.match(html, /href="https:\/\/tiyouju\.com\/"/);
  assert.match(css, /\.update-float-card\s*\{/);
  assert.match(css, /\.update-progress-box\s*\{/);
  assert.match(js, /const showFloatPopup = \(data\) =>/);
  assert.match(js, /showFloatPopup\(data\);/);
  assert.match(js, /\/api\/update\/install\/start/);
  assert.match(js, /\/api\/update\/progress/);
  assert.match(js, /tiyouju-update-dismissed:/);
});
