"use strict";
// 窄屏（<768px）左栏自动收起。
//
// 实测问题：左栏固定 256px（w-64 shrink-0），窄窗口下中栏被挤成一条缝 ——
//   768px 视口：正文 440px，题干每行 26 字，尚可；
//   390px 视口：正文只剩 62px，题干每行 3 个字，一张题卡撑到 1350px 高。
//
// 要求：
//   1. 有窄屏断点与自动收起函数；
//   2. 用户手动开合过就听用户的，不再自动改；
//   3. 窄屏下手动展开的左栏是浮层（position: fixed），不参与 flex 占位，
//      免得展开一次又把正文挤成 62px；
//   4. 断点之上行为不变（不自动收起）。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("窄屏下左栏别再把正文挤成一条缝", async (t) => {
  await t.test("1. 有窄屏断点和自动收起函数", () => {
    assert.ok(
      /const LEFT_SIDEBAR_NARROW_BREAKPOINT = 768;/.test(html),
      "应定义窄屏断点 768"
    );
    const fn = html.match(/function applyResponsiveLeftSidebar\([\s\S]{0,2000}?\n {4}\}/);
    assert.ok(fn, "应能找到 applyResponsiveLeftSidebar");
    assert.ok(
      /window\.innerWidth < LEFT_SIDEBAR_NARROW_BREAKPOINT/.test(fn[0]),
      "必须按视口宽度判断窄屏"
    );
    assert.ok(
      /applyLeftSidebarCollapsed\(true\)/.test(fn[0]),
      "窄屏必须调用现成的收起逻辑（别自己造轮子）"
    );
    assert.ok(
      /leftSidebarNarrowAuto/.test(fn[0]),
      "要记住这次收起是自动做的，回宽屏才能还原"
    );
  });

  await t.test("2. 用户手动开合过就听用户的", () => {
    const fn = html.match(/function toggleLeftSidebar\([\s\S]{0,1200}?\n {4}\}/);
    assert.ok(fn, "应能找到 toggleLeftSidebar");
    assert.ok(
      /leftSidebarUserPref = collapsed;/.test(fn[0]),
      "用户手动开合要记下来，自动逻辑别再跟他打架"
    );
    const resp = html.match(/function applyResponsiveLeftSidebar\([\s\S]{0,2000}?\n {4}\}/);
    assert.ok(
      /leftSidebarUserPref === null/.test(resp[0]),
      "用户没表过态才自动收起"
    );
    assert.ok(
      /leftSidebarUserPref === false|userPref === false/.test(resp[0]),
      "用户在窄屏下明确要展开就必须尊重"
    );
  });

  await t.test("3. 窄屏手动展开的左栏要浮在内容上面", () => {
    const css = html.match(/@media \(max-width: 767px\) \{[\s\S]{0,600}?\}/);
    assert.ok(css, "应能找到 767px 的媒体查询");
    assert.ok(
      /#leftFolderSidebar:not\(\.collapsed\)/.test(css[0]),
      "媒体查询要针对未收起的左栏"
    );
    assert.ok(
      /position:\s*fixed/.test(css[0]),
      "窄屏展开的左栏必须是浮层，不能继续占 flex 宽度"
    );
  });

  await t.test("4. 初始化和窗口缩放都要跟", () => {
    const init = html.match(/async function init\([\s\S]{0,1500}?\n {4}\}/);
    assert.ok(init, "应能找到 init");
    assert.ok(
      /applyResponsiveLeftSidebar\(\);/.test(init[0]),
      "init 里要先按当前宽度收起一次"
    );
    assert.ok(
      /addEventListener\('resize'/.test(init[0]),
      "窗口宽度变了要跟着调整"
    );
  });

  await t.test("5. 断点之上不许自动收起", () => {
    const resp = html.match(/function applyResponsiveLeftSidebar\([\s\S]{0,2000}?\n {4}\}/);
    assert.ok(
      /if \(leftSidebarNarrowAuto\)[\s\S]{0,300}?applyLeftSidebarCollapsed\(false\)/.test(resp[0]),
      "回宽屏要把自动收起的那次还原掉"
    );
  });
});
