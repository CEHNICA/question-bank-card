"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const eagleHtmlPath = path.resolve(__dirname, "eagle/index.html");
const eagleHtml = fs.readFileSync(eagleHtmlPath, "utf8");

test("Eagle 工作台：核验证据能翻到答案印在下一页的那一页", () => {
  // 1. 翻页控件：上一页 / 下一页 / 页码输入 / 总页数 / 回到本题页
  assert.ok(eagleHtml.includes('id="opPageNav"'), "必须有翻页条");
  assert.ok(eagleHtml.includes('id="btnOpPrevPage"'), "必须有上一页按钮");
  assert.ok(eagleHtml.includes('id="btnOpNextPage"'), "必须有下一页按钮");
  assert.ok(eagleHtml.includes('id="opPageInput"'), "必须有页码输入框");
  assert.ok(eagleHtml.includes('id="opPageTotal"'), "必须显示总页数");
  assert.ok(eagleHtml.includes('id="btnOpQuestionPage"'), "必须能一键回到本题所在页");

  // 2. 翻页逻辑
  assert.ok(eagleHtml.includes("fetchOpPageList"), "必须去后端取这份原卷的页码清单");
  assert.ok(eagleHtml.includes("loadOpPageList"), "打开原卷时要加载页码清单");
  assert.ok(eagleHtml.includes("renderOpPageNav"), "必须有翻页条渲染函数");
  assert.ok(eagleHtml.includes("goToOpPage"), "必须有跳到指定页的函数");
  assert.ok(eagleHtml.includes("stepOpPage"), "必须有上一页/下一页的函数");
  assert.ok(eagleHtml.includes("jumpOpPageTo"), "必须有输入页码直接跳转的函数");
  assert.ok(eagleHtml.includes("goToOpQuestionPage"), "必须有回到本题页的函数");

  // 3. 状态里要记住题目所在的那一页：翻去答案页以后还能回来
  assert.ok(eagleHtml.includes("questionPageIdx"), "必须记住这道题所在的那一页");
  assert.ok(eagleHtml.includes("pageList"), "必须记住这份原卷有哪些页");

  // 4. 翻页条是自定义样式：Tailwind 是本地预编译产物，没有现成的工具类
  assert.ok(eagleHtml.includes(".op-page-nav"), "翻页条要有自己的样式");
  assert.ok(eagleHtml.includes(".op-page-btn"), "翻页按钮要有自己的样式");
  assert.ok(eagleHtml.includes(".op-page-input"), "页码输入框要有自己的样式");

  // 5. 键盘：原卷弹窗开着时左右箭头翻页（答案常常在下一页）
  assert.ok(eagleHtml.includes("stepOpPage(e.key === 'ArrowRight' ? 1 : -1)"), "左右箭头要能翻原卷页");
});

test("Eagle 工作台：翻页条不会在只有一页时占地方", () => {
  assert.ok(
    eagleHtml.includes("nav.classList.toggle('hidden', list.length < 2)"),
    "只有一页可翻时整条翻页条要藏起来"
  );
});
