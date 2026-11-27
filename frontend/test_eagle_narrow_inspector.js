"use strict";
// 窄屏下右侧「题目属性」面板展开时不能把正文挤没了。
//
// 实测问题：窄屏下属性栏收成 3rem 窄条，点「›」展开时走 .force-open → width 18rem，
// 而它是 flex 占位（position: static），于是正文被挤：
//   768px 视口：正文 464px → 224px；
//   390px 视口：正文 342px → 102px，题卡只剩 78px，题干挤成每行 3 个字。
//
// 要求：窄屏展开的属性栏必须是浮层（position: fixed），不参与 flex 占位。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("窄屏展开的题目属性栏不许把正文挤成一条缝", async (t) => {
  await t.test("1. 窄屏媒体查询里存在", () => {
    const blocks = html.match(/@media \(max-width: 1100px\) \{[\s\S]{0,2000}?\n {4}\}/g) || [];
    assert.ok(blocks.length >= 2, "应能找到 1100px 的媒体查询");
  });

  await t.test("2. force-open 必须是浮层，不能占 flex 宽度", () => {
    const idx = html.indexOf("@media (max-width: 1100px) {");
    assert.ok(idx > 0, "应能找到媒体查询起点");
    const block = html.slice(idx, idx + 2000);
    const rules = block.match(/#rightInspectorPane\.force-open \{[\s\S]{0,500}?\}/g) || [];
    assert.ok(rules.length >= 1, "应能找到 force-open 的样式");
    const overlay = rules.find(r => /position:\s*fixed/.test(r));
    assert.ok(overlay, "force-open 必须有一条 position: fixed 的浮层规则（否则它是 flex 占位，正文被挤没）");
    assert.ok(
      /right:\s*0/.test(overlay),
      "浮层要贴右边"
    );
    assert.ok(
      /z-index/.test(overlay),
      "浮层要压在内容上面"
    );
  });

  await t.test("3. 宽屏（>1100px）不受影响", () => {
    // 浮层规则必须被包在 max-width 媒体查询里，不能漏到宽屏
    const mediaStart = html.indexOf("@media (max-width: 1100px) {");
    const fixedRule = html.indexOf("position: fixed", mediaStart);
    assert.ok(fixedRule > mediaStart, "浮层规则要写在媒体查询里面");
    // 宽屏折叠走 .collapsed（width: 3rem），逻辑没被改
    assert.ok(
      /#rightInspectorPane\.collapsed \{ width: (?:0|3rem);/.test(html),
      "宽屏的收起规则还在"
    );
  });

  await t.test("4. 展开时正文/属性区该显示出来", () => {
    const idx = html.indexOf("@media (max-width: 1100px) {");
    const block = html.slice(idx, idx + 2000);
    assert.ok(
      /#rightInspectorPane\.force-open #inspectorBody \{ display: block; \}/.test(block),
      "展开后属性正文要显示"
    );
    assert.ok(
      /#rightInspectorPane\.force-open #inspectorHead \{ display: flex; \}/.test(block),
      "展开后属性标题要显示"
    );
  });

  await t.test("5. 点抽屉外面的正文要把抽屉收回去", () => {
    // 页面里有多处 document click 捕获监听（左栏抽屉也有一个），
    // 必须按内容挑出「属性栏」这一个，别靠出现顺序。
    const handlers =
      html.match(/document\.addEventListener\('click',[\s\S]{0,1500}?\}, true\);/g) || [];
    const h = handlers.find((x) => /rightInspectorPane/.test(x));
    assert.ok(h, "应能找到点外面关闭的监听");
    assert.ok(
      /force-open/.test(h),
      "监听要判断属性栏是不是展开状态"
    );
    assert.ok(
      /INSPECTOR_COLLAPSE_BREAKPOINT/.test(h),
      "只在窄屏生效，宽屏别抢点击"
    );
    assert.ok(
      /pane\.contains\(t\)/.test(h),
      "点抽屉自己内部不能关"
    );
    assert.ok(
      /classList\.remove\('force-open'\)/.test(h) && /classList\.add\('collapsed'\)/.test(h),
      "要真的把抽屉收回去"
    );
  });
});
