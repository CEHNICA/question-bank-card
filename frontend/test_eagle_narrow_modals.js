"use strict";
// 窄屏下弹窗与经典库页别再横向溢出。
//
// 实测问题（390px 视口，Playwright 几何 + 外部视觉模型双轨确认）：
//   1. 快速审题弹窗头部右组（上一题/下一题 + 试题篮 + 收藏 + 关闭）372px，
//      弹窗内可用只有 326px —— 「✕」跑到 x=405，屏幕外 15px，点不到。
//   2. 快速审题底部工具条（📄原卷/✎解析/✏️编辑/📷复制图片/📋文本）不换行，
//      「📋 文本」落在 x=340..405，屏幕外 15px。
//   3. 原卷核验的「整页位置 / 只看本题」组被容器裁掉 24px。
//   4. 标签管理的「+ 新建标签」按钮落在 x=380..408，屏幕外 18px。
//   5. 经典库页筛选胶囊组（inline-flex + flex-shrink: 0）不换行，543px 宽，
//      加「智能文件夹」胶囊一路排到 x=631 —— 页面 scrollWidth 631 vs 390，
//      右半截筛选按钮全在屏幕外。
//   6. 经典库页「还有活没干」折叠条 white-space: nowrap，481px 宽，顶到 x=502。
//
// 要求：
//   1. 三个弹窗的窄屏规则都锁在 640px 断点里，并给需要换行的元素加 class 钩子；
//   2. 经典库页的胶囊组与折叠条在 700px 断点里换行；
//   3. 胶囊组那条规则必须放在 eagle-filter-bar.css 末尾 —— 基础规则里
//      flex-shrink: 0 写在前面，同特异性下后来者才生效（放前面会被反覆盖）；
//   4. 宽屏（>700px）行为不变。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");
const filterCss = fs.readFileSync(path.join(__dirname, "eagle-filter-bar.css"), "utf8");
const libraryCss = fs.readFileSync(path.join(__dirname, "library.css"), "utf8");

function mediaBlock(src, bp) {
  // 同一个断点可能有多个 @media 块，全部收进来
  const marker = `@media (max-width: ${bp}px) {`;
  const chunks = [];
  let from = 0;
  for (;;) {
    const start = src.indexOf(marker, from);
    if (start < 0) break;
    const end = src.indexOf("\n    }", start);
    chunks.push(src.slice(start, end < 0 ? start + 4000 : end + 6));
    from = start + marker.length;
  }
  return chunks.join("\n");
}

test("窄屏下弹窗与经典库页别再横向溢出", async (t) => {
  await t.test("1. 弹窗里需要换行的元素都有 class 钩子", () => {
    const hooks = [
      "ql-head-actions", // 快速审题头部右组
      "ql-foot-actions", // 快速审题底部工具条
      "op-head", // 原卷核验顶栏
      "op-toolbar", // 原卷核验工具栏
      "tm-head", // 标签管理头部
      "tm-search-row", // 标签管理搜索行
    ];
    for (const hook of hooks) {
      assert.ok(
        new RegExp(`class="[^"]*\\b${hook}\\b`).test(html),
        `${hook} 要有 class 钩子`
      );
    }
  });

  await t.test("2. 快速审题：头部右组与底部工具条窄屏要能换行", () => {
    const block = mediaBlock(html, 640);
    assert.ok(block, "应能找到 640px 的媒体查询");
    assert.ok(
      /\.ql-head-actions \{[^}]*flex-wrap: wrap;/.test(block),
      "头部右组要允许换行"
    );
    assert.ok(
      /\.ql-head-actions \{[^}]*flex-shrink: 1 !important;/.test(block),
      "头部右组要去掉 shrink-0，否则换不了行"
    );
    assert.ok(
      /\.ql-foot-actions \{[^}]*flex-wrap: wrap;/.test(block),
      "底部工具条要允许换行，否则「📋 文本」会被推出屏幕"
    );
  });

  await t.test("3. 原卷核验与标签管理窄屏要能换行", () => {
    const block = mediaBlock(html, 640);
    assert.ok(
      /\.op-head, \.op-toolbar \{[^}]*flex-wrap: wrap;/.test(block),
      "原卷核验顶栏与工具栏要允许换行"
    );
    assert.ok(
      /\.op-head-left \{[^}]*min-width: 0;/.test(block),
      "顶栏左组要能收缩，长标题才截得掉"
    );
    assert.ok(
      /\.tm-search-row input \{[^}]*width: auto;[^}]*flex: 1 1 120px;/.test(block),
      "标签管理的搜索框别死守 w-48，要能挤开让「+ 新建标签」留在屏内"
    );
    assert.ok(
      /\.tm-body \{ min-width: 0; \}/.test(block),
      "标签管理主体要能收缩"
    );
  });

  await t.test("4. 经典库页：胶囊组与折叠条窄屏要能换行", () => {
    const libBlock = mediaBlock(libraryCss, 700);
    assert.ok(libBlock, "library.css 里应能找到 700px 的媒体查询");
    assert.ok(
      /\.library-todos \.library-todo-summary \{[^}]*white-space: normal;/.test(libBlock),
      "「还有活没干」折叠条不能死守 nowrap"
    );
    assert.ok(
      /\.library-todo-menu \{[^}]*min-width: 0;/.test(libBlock),
      "折叠菜单要能收缩"
    );
  });

  await t.test("5. 胶囊组规则必须放在文件末尾（否则被基础规则反覆盖）", () => {
    const at = filterCss.search(/\.eagle-capsules-group \{[^}]*flex-wrap: wrap;/);
    assert.ok(at > 0, "应能找到胶囊组的换行规则");
    const base = filterCss.search(/\.eagle-capsules-group \{/);
    assert.ok(
      base > 0 && base < at,
      "基础规则（flex-shrink: 0）在前、窄屏规则在后 —— 同特异性下后来者才生效"
    );
    assert.ok(
      /\.eagle-capsules-group \{[^}]*flex-shrink: 1;/.test(filterCss.slice(at - 200, at + 200)),
      "窄屏下胶囊组要能收缩"
    );
    assert.ok(
      /@media \(max-width: 700px\) \{[\s\S]{0,400}?\.eagle-filter-container \{ flex-wrap: wrap;/.test(filterCss),
      "筛选条容器也要允许换行"
    );
  });

  await t.test("6. 宽屏行为不变 —— 规则必须都在媒体查询里", () => {
    const checks = [
      [html, /\.ql-head-actions \{[^}]*flex-wrap: wrap;/, "max-width: 640px"],
      [html, /\.ql-foot-actions \{[^}]*flex-wrap: wrap;/, "max-width: 640px"],
      [html, /\.op-head, \.op-toolbar \{[^}]*flex-wrap: wrap;/, "max-width: 640px"],
      [html, /\.tm-search-row input \{[^}]*width: auto;/, "max-width: 640px"],
      [libraryCss, /\.library-todos \.library-todo-summary \{[^}]*white-space: normal;/, "max-width: 700px"],
      [filterCss, /\.eagle-capsules-group \{[^}]*flex-shrink: 1;/, "max-width: 700px"],
    ];
    for (const [src, rule, bp] of checks) {
      const at = src.search(rule);
      assert.ok(at > 0, `应能找到规则 ${rule}`);
      const before = src.lastIndexOf("@media", at);
      const between = src.slice(before, at);
      assert.ok(
        between.includes(bp),
        `规则 ${rule} 必须写在 ${bp} 的媒体查询里`
      );
    }
  });
});
