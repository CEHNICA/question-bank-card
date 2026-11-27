"use strict";
// 窄屏下工具栏与题卡头部别再挤成一团。
//
// 实测问题（390px 视口，外部视觉模型 + Playwright 几何双轨确认）：
//   1. 顶部一行要塞 ≡ + ◫侧栏 + 搜索 + 筛选 + 篮/全选。中栏只有 342px，
//      搜索框被压到 19px（去掉左边放大镜留白只剩 26px 可输入），
//      还直接压在「筛选」按钮上 —— 视觉模型把「筛选」二字看成了
//      搜索框里的内容，就是压住了。
//   2. 题卡头部左组 + 右组放不下，右组（核验证据 + 来源名）被卡片容器裁掉，
//      768px 视口下实测 5 张卡的右组都被切掉半截。
//   3. 右侧属性栏在窄屏只剩一条 3rem 空条（里面就一个小箭头），
//      390px 视口下白占 48px（屏宽的 12%）。
//
// 要求：
//   1. 窄屏让搜索框独占一行，工具栏其余控件留在上面一行；
//   2. 窄屏让题卡头部右组另起一行，左组在更窄时也能换行收缩；
//   3. 触屏没有 hover，「核验证据」按钮不能永远藏着点不到；
//   4. 窄屏把那条 48px 空条收成 0 宽，切换按钮改成贴边小标签；
//   5. 宽屏（>1100px）行为不变 —— 规则必须都在媒体查询里。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function mediaBlock(bp) {
  // 同一个断点在本文件里可能有多个 @media 块，全部收进来
  const marker = `@media (max-width: ${bp}px) {`;
  const chunks = [];
  let from = 0;
  for (;;) {
    const start = html.indexOf(marker, from);
    if (start < 0) break;
    const end = html.indexOf("\n    }", start);
    chunks.push(html.slice(start, end < 0 ? start + 4000 : end + 6));
    from = start + marker.length;
  }
  return chunks.join("\n");
}

test("窄屏下工具栏与题卡头部别再挤成一团", async (t) => {
  await t.test("1. 四个元素都有 class 钩子", () => {
    assert.ok(
      /class="[^"]*\beagle-toolbar-row\b/.test(html),
      "工具栏那一行要有 eagle-toolbar-row"
    );
    assert.ok(
      /class="[^"]*\beagle-toolbar-left\b/.test(html),
      "工具栏左组要有 eagle-toolbar-left"
    );
    assert.ok(
      /class="[^"]*\beagle-search-slot\b/.test(html),
      "搜索框外层要有 eagle-search-slot"
    );
    assert.ok(
      /class="[^"]*\beagle-toolbar-right\b/.test(html),
      "工具栏右组要有 eagle-toolbar-right"
    );
  });

  await t.test("2. 窄屏搜索框独占一行，不许再压在筛选按钮上", () => {
    const block = mediaBlock(767);
    assert.ok(block, "应能找到 767px 的媒体查询");
    assert.ok(
      /\.eagle-toolbar-row \{ flex-wrap: wrap; \}/.test(block),
      "工具栏要允许换行"
    );
    assert.ok(
      /\.eagle-search-slot \{ order: 10; flex: 1 0 100%; \}/.test(block),
      "搜索框要 order 靠后且占满一整行（basis 100%）"
    );
    assert.ok(
      /\.eagle-toolbar-left \{ display: contents; \}/.test(block),
      "左组要 display: contents，让里面几个控件直接参与外层排版"
    );
  });

  await t.test("3. 题卡头部右组要能另起一行", () => {
    assert.ok(
      /class="[^"]*\bqc-card-head\b/.test(html),
      "题卡头部要有 qc-card-head"
    );
    assert.ok(
      /class="[^"]*\bqc-card-head-left\b/.test(html),
      "头部左组要有 qc-card-head-left"
    );
    assert.ok(
      /class="[^"]*\bqc-card-head-right\b/.test(html),
      "头部右组要有 qc-card-head-right"
    );
    const block = mediaBlock(1100);
    assert.ok(block, "应能找到 1100px 的媒体查询");
    assert.ok(
      /\.qc-card-head \{ flex-wrap: wrap; \}/.test(block),
      "头部要允许换行（右组放不下就自己掉到下一行）"
    );
    assert.ok(
      /\.qc-card-head-right \{ min-width: 0; flex-shrink: 1; \}/.test(block),
      "右组要能收缩，来源名用 truncate 省略而不是被切掉"
    );
    const narrow = mediaBlock(767);
    assert.ok(
      /\.qc-card-head-left \{ flex-shrink: 1 !important; min-width: 0; flex-wrap: wrap; \}/.test(narrow),
      "更窄时左组也要能换行收缩，否则左组自己就撑破卡片"
    );
  });

  await t.test("4. 触屏上核验证据按钮不能藏着", () => {
    assert.ok(
      /class="[^"]*\bqc-evidence-btn\b/.test(html),
      "核验证据按钮要有 qc-evidence-btn"
    );
    assert.ok(
      /@media \(hover: none\) \{[\s\S]{0,300}?\.qc-evidence-btn \{ opacity: 1 !important; \}/.test(html),
      "触屏没有 hover，按钮要常显"
    );
  });

  await t.test("5. 窄屏把右侧那条 48px 空条收掉", () => {
    const narrow = mediaBlock(767);
    assert.ok(
      /#rightInspectorPane:not\(\.force-open\) \{[\s\S]{0,200}?width: 0 !important;/.test(narrow),
      "窄屏收起的属性栏要 0 宽，别白占 48px"
    );
    assert.ok(
      !/#btnToggleInspector/.test(narrow),
      "右栏不再单独放收起按钮，两侧一起由左上角那个按钮开关"
    );
    assert.ok(
      /#rightInspectorPane\.force-open/.test(html),
      "展开态还是走 force-open 那条规则，别被 0 宽覆盖掉"
    );
  });

  await t.test("6. 宽屏行为不变", () => {
    // 上面所有规则都必须锁在媒体查询里
    const narrowRules = [
      /\.eagle-toolbar-row \{ flex-wrap: wrap; \}/,
      /\.qc-card-head \{ flex-wrap: wrap; \}/,
      /\.qc-card-head-left \{ flex-shrink: 1 !important/,
      /\.qc-evidence-btn \{ opacity: 1 !important; \}/,
    ];
    for (const rule of narrowRules) {
      const at = html.search(rule);
      assert.ok(at > 0, `应能找到规则 ${rule}`);
      // 规则之前最近的 @media 必须是窄屏断点（触屏那条是 hover: none）
      const before = html.lastIndexOf("@media", at);
      const between = html.slice(before, at);
      assert.ok(
        /max-width: (767|1100)px|hover: none/.test(between),
        `规则 ${rule} 必须写在媒体查询里`
      );
    }
  });
});
