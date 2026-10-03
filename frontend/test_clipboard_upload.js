"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Upload = require("./app.js");

class FakeFile {
  constructor(parts, name, options = {}) {
    this.parts = parts;
    this.name = name;
    this.type = options.type || "";
    this.lastModified = options.lastModified || 0;
    this.size = parts.reduce((total, part) => total + (part.size || String(part).length), 0);
  }
}

function file(name, type, size = 10) { return { name, type, size, lastModified: 1 }; }

// 输入控件、可编辑区域中的文字粘贴必须保留浏览器原行为。
const input = { closest: () => ({ tagName: "INPUT" }) };
const editable = { closest: (selector) => selector.includes("contenteditable") ? ({}) : null };
const page = { closest: () => null };
assert.equal(Upload.shouldInterceptPaste(input, { files: [file("卷子.pdf", "application/pdf")] }), false);
assert.equal(Upload.shouldInterceptPaste(editable, { files: [file("卷子.pdf", "application/pdf")] }), false);
assert.equal(Upload.shouldInterceptPaste(page, { files: [] }), false);
assert.equal(Upload.shouldInterceptPaste(page, { files: [file("卷子.pdf", "application/pdf")] }), true);

// 多文件按类型交给原有流程；每一类内部和总清单都保持剪贴板顺序。
const mixed = [
  file("甲.pdf", "application/pdf"),
  file("第1页.jpg", "image/jpeg"),
  file("说明.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
  file("第2页.png", "image/png"),
  file("忽略.exe", "application/octet-stream")
];
const routed = Upload.routeFiles(mixed);
assert.deepEqual(routed.documents.map((item) => item.name), ["甲.pdf", "说明.docx"]);
assert.deepEqual(routed.pictures.map((item) => item.name), ["第1页.jpg", "第2页.png"]);
assert.deepEqual(routed.accepted.map((item) => item.name), ["甲.pdf", "第1页.jpg", "说明.docx", "第2页.png"]);
assert.deepEqual(routed.unsupported.map((item) => item.name), ["忽略.exe"]);

// 浏览器给截图的通用 image.png 名称会换成清楚且不冲突的时间名称。
const now = new Date(2026, 8, 27, 14, 5, 9);
const screenshots = Upload.prepareClipboardFiles([
  file("image.png", "image/png", 20),
  file("", "image/jpeg", 30),
  file("已有名称.webp", "image/webp", 40)
], now, FakeFile);
assert.equal(screenshots[0].name, "剪贴板截图-20260927-140509.png");
assert.equal(screenshots[1].name, "剪贴板截图-20260927-140509-2.jpg");
assert.equal(screenshots[2].name, "已有名称.webp");

// 取消确认必须停在本机预览，不能进入上传函数。
(async () => {
  let uploads = 0;
  const accepted = [file("卷子.pdf", "application/pdf")];
  const completed = await Upload.runConfirmedPaste(accepted, async () => false, async () => { uploads += 1; });
  assert.equal(completed, false);
  assert.equal(uploads, 0);

  let received = null;
  const uploaded = await Upload.runConfirmedPaste(accepted, async () => true, async (files) => { received = files; });
  assert.equal(uploaded, true);
  assert.equal(received, accepted);

  // 所有上传入口共用同一个资料类型选择；文档和照片表单都会把它交给后端。
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
  assert.match(html, /name="materialType" value="exam" checked/);
  assert.match(html, /name="materialType" value="book"/);
  assert.match(html, /id="bookChunkHint"[\s\S]*?每 100 页稳定分片[\s\S]*?原文件不会改动/);
  assert.match(css, /\.material-hint\s*\{/);
  // Capture the mode/type when opening the photo preview: changing the sidebar
  // afterwards must never quietly dispatch a cloud upload.
  assert.match(js, /const materialType = selectedMaterialType\(\)/);
  assert.match(js, /form\.append\("material_type", materialType\)/);
  assert.match(js, /form\.append\("material_type", photoUpload\.materialType\)/);
  assert.match(js, /photoUpload\.parseMode = parseMode/);
  assert.match(js, /if \(!\$\("photoDialog"\)\.open\) \$\("photoDialog"\)\.showModal\(\)/);

  console.log("clipboard upload regression checks: OK");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
