// 官网静态版本号兜底的单元测试：注入函数必须只改版本号、不碰正文。
//
// 跑法（仓库根目录）：
//     node --test website/test_version_inject.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { injectVersion, extractVersion } from "./lib/version.mjs";

const BADGE = '<span class="version-badge" id="versionBadge">v2.3.12</span>';
const META = [
  '<p class="release-meta" id="releaseMeta">',
  " 当前最新：<strong>v2.3.12</strong> · 点击即可直接下载安装包，自动同步 GitHub 最新正式版 ",
  "</p>",
].join("\n");
const OTHER_STRONG = "<strong>更多信息 → 仍要运行</strong>";

const PAGE = `<!doctype html><html><body>${BADGE}${META}${OTHER_STRONG}</body></html>`;

test("徽章与 hero 文案都换成真实最新版", () => {
  const out = injectVersion(PAGE, "v2.3.16");
  assert.ok(out.includes('<span class="version-badge" id="versionBadge">v2.3.16</span>'));
  assert.ok(out.includes("<strong>v2.3.16</strong>"));
  assert.ok(!out.includes("v2.3.12"));
});

test("没有 v 前缀的版本号也会补上", () => {
  const out = injectVersion(PAGE, "2.3.16");
  assert.ok(out.includes(">v2.3.16<"));
  assert.ok(!out.includes("v2.3.12"));
});

test("页面上其它 strong 不受牵连", () => {
  const out = injectVersion(PAGE, "v2.3.16");
  assert.ok(out.includes(OTHER_STRONG));
});

test("版本号取不到时原样返回", () => {
  assert.equal(injectVersion(PAGE, ""), PAGE);
  assert.equal(injectVersion(PAGE, null), PAGE);
  assert.equal(injectVersion(PAGE, undefined), PAGE);
});

test("页面结构不匹配时不乱改", () => {
  const odd = "<html><body><p id=\"releaseMeta\">没有 strong 的段落</p></body></html>";
  assert.equal(injectVersion(odd, "v2.3.16"), odd);
});

test("占位内容不是版本号形状时不动它", () => {
  const odd = '<span class="version-badge" id="versionBadge">vNEXT</span>';
  assert.equal(injectVersion(odd, "v2.3.16"), odd);
});

test("能读回注入后的版本号", () => {
  const out = injectVersion(PAGE, "v2.3.16");
  assert.equal(extractVersion(out), "v2.3.16");
  assert.equal(extractVersion(PAGE), "v2.3.12");
  assert.equal(extractVersion("<html></html>"), "");
});
