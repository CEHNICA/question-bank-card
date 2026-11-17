const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

describe("录入题目页（/）重设计契约检查", () => {
  it("index.html 包含纯图标侧栏折叠、章节目录、右上角题库工作台+设置与显式试卷操作按钮，并已移除聚光灯模式按钮", () => {
    assert.match(html, /id="entrySidebarToggle"[^>]*aria-label="收起待录列表"/);
    assert.doesNotMatch(html, /id="entrySidebarToggle"[\s\S]*?>试卷栏</);
    assert.doesNotMatch(html, /id="focusToggle"/);
    assert.doesNotMatch(html, /id="settingsFocus"/);
    assert.match(html, /class="review-top-actions review-top-actions-end[\s\S]*?id="reviewReturnLibrary"[\s\S]*?id="settingsButton"/);
    assert.match(html, /id="entryChapterNav"[^>]*hidden/);
    assert.match(html, /id="entryChapterToggle"/);
    assert.match(html, /id="entryChapterList"/);
    assert.match(html, /id="paperMenuTrigger"[^>]*aria-controls="paperMenu"/);
    assert.match(html, /<details[^>]*id="paperMenu"/);
    assert.match(html, /id="settingsRename"/);
    assert.match(html, /id="approveAllGreen"/);
    assert.match(html, /id="settingsArchive"/);
    assert.match(html, /id="settingsDelete"/);
  });

  it("styles.css 保持 820px 以上并排核对、紧凑切题条、无顶部阴影操作条与静止对勾显色", () => {
    assert.doesNotMatch(css, /@media\s*\(max-width:\s*1100px\)/);
    assert.match(css, /@media\s*\(max-width:\s*820px\)/);
    assert.match(css, /:root\.entry-sidebar-collapsed\s+\.sidebar\s*\{/);
    assert.match(css, /\.paper-menu-trigger\s*\{/);
    assert.match(css, /\.entry-chapter-nav\s*\{/);
    assert.match(css, /\.cut-reading-stage\s*\{[\s\S]*?grid-template-areas:/);
    assert.match(css, /\.card:not\(\.compact\)\s+\.card-actions\s*\{[\s\S]*?position:\s*sticky;[\s\S]*?bottom:\s*0/);
    assert.match(css, /\.card:not\(\.compact\)\s+\.card-actions\s*\{[\s\S]*?box-shadow:\s*none;/);
    // 静止对勾必须显色。旧值 #94a3b8 在白底只有 2.56:1，已改成达标的色值，
    // 具体色值与对比度由 test_readability_contrast.js 按 WCAG AA 守，这里只管「有没有显色」。
    assert.match(css, /\.card-tick\s*\{[\s\S]*?color:\s*#[0-9a-fA-F]{3,6};/);
  });

  it("app.js 挂载试卷操作按钮、侧栏折叠及对应试卷下挂载多章节导航", () => {
    assert.match(appJs, /function\s+renderEntryChapterNav\s*\(/);
    assert.match(appJs, /function\s+jumpToChapterQuestion\s*\(/);
    assert.match(appJs, /pendingChapterJumpId/);
    assert.match(appJs, /function\s+syncActiveChapterNav\s*\(/);
    assert.match(appJs, /function\s+setEntrySidebarCollapsed\s*\(/);
    assert.match(appJs, /if\s*\(isActive\s*&&\s*chapterNav\)\s*\{[\s\S]*?item\.append\(chapterNav\)/);
    assert.match(appJs, /\$\("entryChapterToggle"\)\?\.addEventListener\("click"/);
    assert.match(appJs, /\$\("paperMenuTrigger"\)\?\.addEventListener\("click"/);
    assert.match(appJs, /\$\("entrySidebarToggle"\)\?\.addEventListener\("click"/);
    assert.match(appJs, /renderEntryChapterNav\(\);/);
    assert.match(appJs, /syncActiveChapterNav\(id\)/);
  });
});
