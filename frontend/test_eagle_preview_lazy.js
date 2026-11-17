"use strict";
// 预览框懒加载：首屏别把打印预览那一整套排版资源先拉下来。
//
// 浏览器不管外层是不是 display:none，只要 <iframe> 上有 src 就照样下载。
// Eagle 页的「试题篮 → A4 预览」是个隐藏模态框，里面挂着一个 iframe，
// src 直接写在 HTML 里 —— 于是每次打开题库工作台，都会先来一整套：
//   /eagle-print-preview.html   2.2 KB
//   /styles.css               197 KB 明文 / 44 KB gzip
//   /library.css              185 KB 明文 / 44 KB gzip
//   /exam-layout.js            29 KB 明文 / 10 KB gzip
//   /library-solutions.js        9 KB 明文 /  3 KB gzip
//   /eagle-print-preview.js     19 KB 明文 /  6 KB gzip
// 约 110 KB gzip，全是首屏一道题都还没看见就要下的东西，还跟题库数据抢带宽。
//
// 要求：iframe 的 src 留空，第一次打开预览时才填。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("A4 预览框要懒加载，别在首屏就把排版资源拉下来", async (t) => {
  await t.test("1. iframe 不能写死 src", () => {
    const tag = html.match(/<iframe[^>]*id="eaglePreviewFrame"[\s\S]{0,400}?>/);
    assert.ok(tag, "应能找到预览框");
    assert.ok(
      !/\ssrc\s*=/.test(tag[0]),
      "预览框不能写死 src（hidden 的模态框也照样会下载）"
    );
    assert.ok(
      /data-src\s*=\s*"\/eagle-print-preview\.html"/.test(tag[0]),
      "src 要挪到 data-src，等打开时再填"
    );
  });

  await t.test("2. 必须有加载预览框的函数", () => {
    const fn = html.match(/function ensurePreviewFrameLoaded\([\s\S]{0,600}?\n {4}\}/);
    assert.ok(fn, "应能找到 ensurePreviewFrameLoaded");
    assert.ok(
      /setAttribute\('src'/.test(fn[0]),
      "ensurePreviewFrameLoaded 必须真的把 src 填上"
    );
    assert.ok(
      /dataset\.src/.test(fn[0]),
      "ensurePreviewFrameLoaded 必须从 data-src 取地址"
    );
  });

  await t.test("3. 打开预览时必须调用它", () => {
    const fn = html.match(/async function openBasketPreview\([\s\S]{0,1500}?\n {4}\}/);
    assert.ok(fn, "应能找到 openBasketPreview");
    assert.ok(
      /ensurePreviewFrameLoaded\(\)/.test(fn[0]),
      "openBasketPreview 必须先把预览框拉起来"
    );
  });

  await t.test("4. 预览框还没就绪时别发空篮子过去", () => {
    const fn = html.match(/window\.addEventListener\('message'[\s\S]{0,2000}?\}\);/);
    assert.ok(fn, "应能找到 message 监听");
    assert.ok(
      /qbc:eagle-print-preview-ready[\s\S]{0,300}?if \(previewState\.items\.length\) postPreviewPayload\(\);/.test(fn[0]),
      "就绪时手里还没题就不该发空篮子（状态栏会闪一句假消息）"
    );
  });

  await t.test("5. 预览页自己那份资源清单不许回到主页面", () => {
    // 主页面只链 tailwind.css 和 katex.css；styles.css / library.css 是预览页的。
    const head = html.slice(0, html.indexOf("</head>"));
    assert.ok(
      !/<link[^>]*href=["']\/styles\.css/.test(head),
      "主页面不该链 styles.css（那是预览页的排版样式）"
    );
    assert.ok(
      !/<link[^>]*href=["']\/library\.css/.test(head),
      "主页面不该链 library.css（那是预览页的排版样式）"
    );
  });
});
