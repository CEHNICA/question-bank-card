"use strict";
// 窄屏（<768px）下左栏抽屉和右侧属性浮层不许同时开。
//
// 实测死锁（390×844 视口，Playwright 几何 + elementFromPoint 命中测试确认）：
//   左栏抽屉  position:fixed，占 0..256px；
//   右栏属性  position:fixed，占 102..390px（宽 288px）。
// 两个浮层重叠 154px，于是：
//   · 抽屉自己的「收起左栏」按钮(223..247px) 被右栏整个盖住 → 点不到；
//   · 中栏工具栏的「◫ 侧栏」按钮(12..60px) 被抽屉盖住 → 也点不到。
// 用户一旦同时开出这两个浮层，页面上就没有任何一个出口能把它们关掉，
// 只能刷新 —— 这不是难用，是死局。
//
// 要求：
//   1. 窄屏下开左栏抽屉要顺手关掉右栏属性浮层；
//   2. 窄屏下开右栏属性浮层要顺手关掉左栏抽屉；
//   3. 万一互斥逻辑没生效，抽屉的 z-index 必须高于属性浮层，
//      抽屉自己得永远是压在上面的那个（它内部有出口）；
//   4. 窄屏抽屉没有遮罩，点中栏空白处要能把它收回去（跟属性浮层一个规矩）；
//   5. 窄屏顶栏/抽屉头的触摸目标不许小于 32px（手机上 24px 点不准）；
//   6. 宽屏行为零变化 —— 新规则必须全部锁在窄屏媒体查询里。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const HTML_PATH =
  process.env.QB_EAGLE_HTML || path.join(__dirname, "eagle", "index.html");
const html = fs.readFileSync(HTML_PATH, "utf8");

function fnBody(name, span = 2500) {
  const m = html.match(new RegExp(`function ${name}\\([\\s\\S]{0,${span}}?\\n    \\}`));
  assert.ok(m, `应能找到 ${name}`);
  return m[0];
}

// 同一个断点在本文件里可能有多个 @media 块，全部收进来
function mediaBlock(bp) {
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

test("窄屏下两个浮层不许互相盖死", async (t) => {
  await t.test("1. 开左栏抽屉要关掉右栏属性浮层", () => {
    const fn = fnBody("toggleLeftSidebar");
    assert.ok(
      /closeNarrowRightInspectorOverlay\(\)/.test(fn),
      "toggleLeftSidebar 必须调用关属性浮层的逻辑"
    );
    assert.ok(
      /LEFT_SIDEBAR_NARROW_BREAKPOINT/.test(fn),
      "互斥只在窄屏生效，宽屏两个栏可以并存"
    );
  });

  await t.test("2. 开右栏属性浮层要关掉左栏抽屉", () => {
    const fn = fnBody("toggleRightInspector");
    assert.ok(
      /leftFolderSidebar/.test(fn),
      "toggleRightInspector 必须管到左栏抽屉"
    );
    assert.ok(
      /toggleLeftSidebar\(\)/.test(fn),
      "开属性栏前要把左栏抽屉收掉"
    );
    assert.ok(
      /LEFT_SIDEBAR_NARROW_BREAKPOINT/.test(fn),
      "互斥只在窄屏生效"
    );
  });

  await t.test("3. 关属性浮层要用现成的收起语义", () => {
    const fn = fnBody("closeNarrowRightInspectorOverlay");
    assert.ok(
      /classList\.remove\('force-open'\)/.test(fn) && /classList\.add\('collapsed'\)/.test(fn),
      "要走 force-open → collapsed 这条既有路径"
    );
    assert.ok(
      /syncInspectorToggleButton\(\)/.test(fn),
      "收起后要同步切换按钮的箭头和提示"
    );
  });

  await t.test("4. 抽屉的 z-index 必须高于属性浮层", () => {
    const narrow = mediaBlock(767);
    const drawer = narrow.match(/#leftFolderSidebar:not\(\.collapsed\) \{[\s\S]{0,400}?\}/);
    assert.ok(drawer, "应能找到窄屏抽屉的样式");
    const z = drawer[0].match(/z-index:\s*(\d+)/);
    assert.ok(z, "窄屏抽屉要写死 z-index");
    const rightPaneRules =
      html.match(/#rightInspectorPane\.force-open \{[^}]*\}/g) || [];
    const rightPane = rightPaneRules.find((r) => /z-index/.test(r));
    assert.ok(rightPane, "应能找到属性浮层的样式");
    const zr = rightPane.match(/z-index:\s*(\d+)/);
    assert.ok(zr, "属性浮层要写死 z-index");
    assert.ok(
      Number(z[1]) > Number(zr[1]),
      `抽屉 z-index(${z[1]}) 必须大于属性浮层(${zr[1]})，`
      + "不然抽屉的「收起」按钮会被盖住，用户没有出口"
    );
  });

  await t.test("5. 窄屏点抽屉外面要能收回去", () => {
    const handlers = html.match(
      /document\.addEventListener\('click',[\s\S]{0,1500}?\}, true\);/g
    ) || [];
    const drawer = handlers.find((h) => /leftFolderSidebar/.test(h));
    assert.ok(drawer, "应能找到左栏抽屉的外部点击监听");
    assert.ok(
      /LEFT_SIDEBAR_NARROW_BREAKPOINT/.test(drawer),
      "只在窄屏生效，宽屏别抢点击"
    );
    assert.ok(
      /side\.contains\(t\)/.test(drawer),
      "点抽屉自己内部不能关"
    );
    assert.ok(
      /toggleLeftSidebar\(\)/.test(drawer),
      "要真的把抽屉收回去"
    );
    assert.ok(
      !/toggleLeftSidebar\(\)/.test(handlers.find((h) => /rightInspectorPane/.test(h)) || ""),
      "属性浮层的监听别改成动左栏（两套监听各管各的）"
    );
  });

  await t.test("6. 窄屏触摸目标不许小于 32px", () => {
    const narrow = mediaBlock(767);
    assert.ok(
      /\.eagle-toolbar-row button/.test(narrow),
      "顶栏按钮要有窄屏触摸目标规则"
    );
    assert.ok(
      /min-height:\s*32px/.test(narrow),
      "窄屏触摸目标高度要提到 32px"
    );
    assert.ok(
      /input:not\(\[type="checkbox"\]\)/.test(narrow),
      "勾选框不能跟着放大（会撑破卡片头/工具栏排版）"
    );
    assert.ok(
      /id="leftSidebarHead"/.test(html),
      "抽屉头部要有 id，触摸目标规则才挂得上"
    );
    assert.ok(
      /#leftSidebarHead button/.test(narrow),
      "抽屉头的按钮也要放大"
    );
  });

  await t.test("7. 宽屏零变化", () => {
    const narrow = mediaBlock(767);
    // 触摸目标规则必须在 767px 媒体查询里
    const at = narrow.indexOf("min-height: 32px");
    assert.ok(at > 0, "应有 min-height: 32px 规则");
    // 抽屉 z-index 规则必须在 767px 媒体查询里
    const zAt = html.indexOf("z-index: 70");
    assert.ok(zAt > 0, "窄屏抽屉 z-index 规则应存在");
    const before = html.lastIndexOf("@media", zAt);
    assert.ok(
      /max-width: 767px/.test(html.slice(before, zAt)),
      "z-index: 70 必须写在 767px 媒体查询里，宽屏左栏不该被改"
    );
    // 宽屏下左栏仍是普通 flex 占位
    assert.ok(
      /#leftFolderSidebar\.collapsed \{ width: 0 !important;/.test(html),
      "宽屏的收起规则还在"
    );
    assert.ok(
      !/min-height:\s*32px/.test(html.slice(0, html.indexOf("@media (max-width: 767px) {"))),
      "min-height: 32px 不能出现在窄屏媒体查询之前（会漏到宽屏）"
    );
  });
});
