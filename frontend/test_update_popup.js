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
    "eagleUpdatePopupInstall",
    "eagleUpdatePopupLater",
    "eagleUpdatePopupClose",
    "eagleUpdatePopupAbout",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `Missing #${id} in eagle/index.html`);
  }
  assert.match(html, /function showEagleUpdatePopup\(data\)/);
  assert.match(html, /function applyOfficialUpdateState\(data/);
  assert.match(html, /\/api\/update\/install/);
  assert.match(html, /\/api\/update\/status/);
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
    "updateFloatInstall",
    "updateFloatLater",
    "updateFloatClose",
    "updateFloatAbout",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `Missing #${id} in frontend/index.html`);
  }
  assert.match(css, /\.update-float-card\s*\{/);
  assert.match(js, /const showFloatPopup = \(data\) =>/);
  assert.match(js, /showFloatPopup\(data\);/);
  assert.match(js, /tiyouju-update-dismissed:/);
});
