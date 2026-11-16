"use strict";
// 标签地图深链：Eagle 页那个「📒 展开标签地图」按钮以前是条死链。
//
// 实测（Playwright，390px 与 1440px 两档）：
//   打开 /library?tag_map=1 —— 页面 DOM 与完全不带参数的 /library 一模一样，
//   .tag-map-workspace 根本没渲染（false）。按钮 title 写着「在经典题库页
//   打开标签地图」，实际落到地上是普通题库页。
//   原因：?tag_map= 这个参数全仓没有任何代码消费过。
//
// 要求：
//   1. 经典库页要认 ?tag_map=，直接开标签管理面板的地图视图；
//   2. ?tag_map=1 表示「不指定标签」，?tag_map=<标签 id> 表示聚焦到那个标签；
//   3. Eagle 页那边仍然要带这个参数（那边已经带上了，别改坏）。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const libraryJs = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("标签地图深链要真的能落到标签地图上", async (t) => {
  await t.test("1. 经典库页要消费 tag_map 参数", () => {
    assert.ok(
      /params\.get\("tag_map"\)/.test(libraryJs),
      "library.js 要读 tag_map 参数"
    );
    assert.ok(
      /openTagManager\(\{ view: "map"/.test(libraryJs),
      "拿到参数要直接开标签管理面板的地图视图"
    );
  });

  await t.test("2. tag_map=1 表示不指定标签，带 id 表示聚焦那个标签", () => {
    assert.ok(
      /tagMapParam === "1" \? undefined : tagMapParam/.test(libraryJs),
      "?tag_map=1 是「打开地图」；?tag_map=<id> 是「打开地图并聚焦这个标签」"
    );
  });

  await t.test("3. Eagle 页那个按钮仍然带这个参数", () => {
    assert.ok(
      /function openTagMapFromTree\(tagId\)/.test(eagleHtml),
      "Eagle 页要有 openTagMapFromTree"
    );
    assert.ok(
      /'\?tag_map=' \+ encodeURIComponent/.test(eagleHtml),
      "Eagle 页要拼出 ?tag_map= 参数"
    );
    assert.ok(
      /window\.open\(base \+ '\/library' \+ qs, '_blank', 'noopener'\)/.test(eagleHtml),
      "Eagle 页要在新标签页打开经典库页"
    );
  });
});
