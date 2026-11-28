const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");

describe("录入题目页（/）重设计契约检查", () => {
  it("index.html 移除冗余的左侧收起按钮（已有全屏放大审题），并包含章节目录、右上角题库工作台+设置与显式试卷操作按钮", () => {
    assert.doesNotMatch(html, /id="entrySidebarToggle"/);
    assert.doesNotMatch(html, /id="focusToggle"/);
    assert.doesNotMatch(html, /id="settingsFocus"/);
    assert.doesNotMatch(html, /<header\s+class="topbar/);
    assert.match(html, /class="entry-sidebar-nav"[\s\S]*?id="reviewReturnLibrary"[\s\S]*?id="settingsButton"/);
    assert.match(html, /id="settingsButton"[\s\S]*?<\/div>\s*<div class="sidebar-scroll">/, "题库工作台和设置留在侧栏滚动区外面");
    assert.match(css, /body\.entry-page \.sidebar\s*\{[\s\S]*?overflow:\s*hidden;/);
    assert.match(css, /body\.entry-page \.sidebar-scroll\s*\{[\s\S]*?overflow-y:\s*auto;/);
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

  it("app.js 挂载试卷操作按钮及对应试卷下挂载多章节导航，并保留全屏审题", () => {
    assert.match(appJs, /function\s+renderEntryChapterNav\s*\(/);
    assert.match(appJs, /function\s+jumpToChapterQuestion\s*\(/);
    assert.match(appJs, /pendingChapterJumpId/);
    assert.match(appJs, /function\s+syncActiveChapterNav\s*\(/);
    assert.match(appJs, /if\s*\(isActive\s*&&\s*chapterNav\)\s*\{[\s\S]*?item\.append\(chapterNav\)/);
    assert.match(appJs, /\$\("entryChapterToggle"\)\?\.addEventListener\("click"/);
    assert.match(appJs, /\$\("paperMenuTrigger"\)\?\.addEventListener\("click"/);
    assert.match(appJs, /renderEntryChapterNav\(\);/);
    assert.match(appJs, /syncActiveChapterNav\(id\)/);
  });

  it("空状态直达上传、多照片弹窗支持调序与移除、窄屏不溢出且帮助文案与 API 配置一致", () => {
    assert.match(html, /id="emptyUploadButton"[^>]*class="button primary"[^>]*>选择文件或照片上传<\/button>/);
    assert.doesNotMatch(html, /设置 → 服务与密钥/);
    assert.match(appJs, /\$\("emptyUploadButton"\)\?\.addEventListener\("click"/);
    assert.match(appJs, /function\s+movePendingPhoto\s*\(/);
    assert.match(appJs, /function\s+removePendingPhoto\s*\(/);
    assert.match(appJs, /function\s+clearStaleReviewPaper\s*\(/);
    assert.match(css, /\.paper-item,\s*\.paper-link,\s*\.entry-chapter-nav\s*\{\s*min-width:\s*0;\s*max-width:\s*100%;\s*\}/);
    const apiCss = fs.readFileSync(path.join(__dirname, "api-redesign.css"), "utf8");
    assert.match(apiCss, /@media\s*\(max-width:\s*760px\)/);
  });
});
