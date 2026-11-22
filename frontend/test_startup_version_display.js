const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const ROOT = __dirname;

test("工作台刚打开时左栏底部与主菜单显示软件版本号", () => {
  const html = fs.readFileSync(path.join(ROOT, "eagle", "index.html"), "utf8");
  assert.match(html, /id="eagleSidebarVersionFooter"/);
  assert.match(html, /id="eagleVersionBadge"[^>]*href="\/settings#settingsAbout"/);
  assert.match(html, /id="eagleMenuVersionPill"/);
  assert.match(html, /async function loadEagleAppVersion\(\)/);
  assert.match(html, /fetch\('\/api\/status'/);
  assert.match(html, /void loadEagleAppVersion\(\);/);
});

test("录入首页顶栏品牌区随 /api/status 显示版本号", () => {
  const indexHtml = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
  const stylesCss = fs.readFileSync(path.join(ROOT, "styles.css"), "utf8");
  const appJs = fs.readFileSync(path.join(ROOT, "app.js"), "utf8");

  assert.match(indexHtml, /id="entryTopbarVersion"/);
  assert.match(stylesCss, /\.entry-topbar-version\s*\{/);
  assert.match(appJs, /document\.getElementById\("entryTopbarVersion"\)/);
  assert.match(appJs, /topVersion\.textContent = `v\$\{s\.app_version\}`/);
});
