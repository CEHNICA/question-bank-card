"use strict";
// 标签地图深链：经典库页仍认 ?tag_map=，直接开标签管理面板的地图视图。
// Eagle 侧栏已不再放「展开标签地图」入口；旧 openTagMapFromTree 改为打开 ≡ 菜单同款标签管理。
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

  await t.test("3. Eagle 不再用侧栏按钮跳经典库地图，旧入口改走标签管理", () => {
    assert.doesNotMatch(
      eagleHtml,
      /展开标签地图/,
      "Eagle 侧栏不得再显示「展开标签地图」"
    );
    assert.match(
      eagleHtml,
      /function openTagMapFromTree\(tagId\)\s*\{[\s\S]*?openTagManagerModal\(\);/,
      "旧 openTagMapFromTree 应改开标签管理中心"
    );
    assert.doesNotMatch(
      eagleHtml,
      /function openTagMapFromTree\(tagId\)\s*\{[\s\S]*?window\.open\(base \+ '\/library'/,
      "Eagle 不得再新开 /library?tag_map= 跳经典库地图"
    );
  });
});
