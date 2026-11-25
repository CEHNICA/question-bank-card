const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const ROOT = __dirname;

test("工作台版本号只在 ≡ 主菜单显示，左下角脚标已隐藏", () => {
  const html = fs.readFileSync(path.join(ROOT, "eagle", "index.html"), "utf8");
  assert.match(html, /id="eagleSidebarVersionFooter"[^>]*\bhidden\b/);
  assert.match(html, /id="eagleMenuVersionPill"/);
  assert.match(html, /async function loadEagleAppVersion\(\)/);
  assert.match(html, /fetch\('\/api\/status'/);
  assert.match(html, /void loadEagleAppVersion\(\);/);
  assert.match(html, /menuPill\.textContent = `v\$\{ver\}`/);
  assert.doesNotMatch(
    html,
    /badge\.textContent = `v\$\{ver\}`/,
    "左下角版本徽标不再写入文案"
  );
  assert.match(html, /\.eagle-q-tag:hover/);
  assert.doesNotMatch(html, /eagle-q-tag-wiggle/, "悬停不再用抖动动画");
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
