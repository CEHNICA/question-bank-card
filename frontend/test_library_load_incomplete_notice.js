"use strict";
// 题库后台补拉中断的守护测试（第十一轮：空态 / 错误态 / 加载态反馈一致性）。
//
// 实测发现的真缺陷（Playwright 真实浏览器 + 故障注入）：
//   首屏只拉 150 道题（/api/library?bulk=1&limit=150），剩下的在后台补拉
//   （/api/library?bulk=1&limit=1000）。后台那条一失败（断网/500），旧版是：
//     - catch 里只 console.warn，然后把 libraryBackgroundPending 置 false 走正常收尾；
//     - 非 2xx 直接 `break`，items 为空也直接 `break` —— 两条路都当"加载完了"处理；
//     - 于是界面上写「已呈现全部 150 道试题」，而库里其实有 810 道。
//   用户看到的就是"题库就这么大"，或者"数据丢了"，没有任何一处提示说
//   还有 660 道没载入。老子实测：sentinelText='向下滚动加载更多 (25 / 150)...'，
//   toast 无、按钮无。
//
// 修复后的实测：
//   sentinelText='题库没有加载完：已载入 150 / 810 道，还有 660 道没载入'
//   +「重新加载剩余试题」按钮 + 错误 toast。
//
// 静态源码断言：喂修复前的 HTML（--html=）进来必须变红。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
}

const eaglePath = arg("html") || path.join(__dirname, "eagle", "index.html");
const eagle = fs.readFileSync(eaglePath, "utf8");

/** 取某个 try/catch 块（花括号配平），按关键词定位 try。 */
function blockOf(src, keyword, startAt = 0) {
  const idx = src.indexOf(keyword, startAt);
  if (idx < 0) return null;
  const i = src.indexOf("{", idx);
  if (i < 0) return null;
  let depth = 0;
  let j = i;
  while (j < src.length) {
    if (src[j] === "{") depth += 1;
    else if (src[j] === "}") {
      depth -= 1;
      if (depth === 0) break;
    }
    j += 1;
  }
  return src.slice(i, j + 1);
}

// ---------------------------------------------------------------- 断言

it("尺子自检：后台补拉函数必须找得到", () => {
  const fn = blockOf(eagle, "async function loadRemainingLibraryItems");
  assert.ok(fn, "找不到 loadRemainingLibraryItems（HTML 结构变了？）");
  assert.ok(
    fn.includes("/api/library?bulk=1&limit=1000"),
    "后台补拉请求的 URL 变了，这条测试要跟着更新"
  );
});

it("后台补拉遇到非 2xx 必须当失败处理，不能 break 掉", () => {
  const fn = blockOf(eagle, "async function loadRemainingLibraryItems");
  assert.ok(fn, "找不到 loadRemainingLibraryItems");
  assert.ok(
    /if\s*\(\s*!res\.ok\s*\)\s*throw/.test(fn),
    "后台补拉遇到非 2xx 必须 throw：break 会走正常收尾，" +
      "界面上写「已呈现全部 N 道试题」，等于拿假话盖住失败"
  );
});

it("后台补拉拿到空列表必须当失败处理，不能 break 掉", () => {
  const fn = blockOf(eagle, "async function loadRemainingLibraryItems");
  assert.ok(fn, "找不到 loadRemainingLibraryItems");
  assert.ok(
    /!Array\.isArray\(page\.items\)\s*\|\|\s*!page\.items\.length\s*\)\s*\{?\s*throw/.test(fn),
    "后台补拉拿到空 items 必须 throw：静默 break 等于把断档当成加载完了"
  );
});

it("后台补拉失败的 catch 必须给出用户可见的中断提示", () => {
  const fn = blockOf(eagle, "async function loadRemainingLibraryItems");
  assert.ok(fn, "找不到 loadRemainingLibraryItems");
  const catchIdx = fn.indexOf("catch");
  assert.ok(catchIdx > 0, "后台补拉没有 catch 分支");
  const catchBody = fn.slice(catchIdx);
  assert.ok(
    /showLibraryLoadIncompleteNotice\s*\(/.test(catchBody),
    "后台补拉失败的 catch 里必须调用 showLibraryLoadIncompleteNotice，" +
      "否则用户看到的是「已呈现全部 150 道试题」，而库里其实有 810 道"
  );
  // 提示函数本身必须真的给出用户可见反馈（toast），不能只是个空壳标记。
  const notice = blockOf(eagle, "function showLibraryLoadIncompleteNotice");
  assert.ok(notice, "找不到 showLibraryLoadIncompleteNotice 的函数体");
  assert.ok(
    /showToast\s*\(/.test(notice),
    "中断提示必须有 toast 形式的反馈"
  );
  assert.ok(
    /renderLibraryLoadIncompleteNotice\s*\(/.test(notice),
    "中断提示必须同时刷新列表底部的提示条"
  );
});

it("必须提供「重新加载剩余试题」的重试入口", () => {
  assert.ok(
    /function\s+retryRemainingLibraryLoad\s*\(/.test(eagle),
    "缺少 retryRemainingLibraryLoad：提示了却没法自救等于半个修复"
  );
  const fn = blockOf(eagle, "function retryRemainingLibraryLoad");
  assert.ok(fn, "retryRemainingLibraryLoad 找不到函数体");
  assert.ok(
    /loadRemainingLibraryItems\s*\(/.test(fn),
    "重试必须接着 loadRemainingLibraryItems 拉剩下的题"
  );
});

it("列表底部提示条必须优先显示中断提示，不能先写「已呈现全部」", () => {
  const fn = blockOf(eagle, "function renderNextQuestionChunk");
  assert.ok(fn, "找不到 renderNextQuestionChunk");
  const checks = fn.match(/libraryLoadIncomplete/g) || [];
  assert.ok(
    checks.length >= 2,
    "renderNextQuestionChunk 里至少要有两处 libraryLoadIncomplete 判断" +
      "（滚动到底一处、追加完一批一处），否则滚动一下提示就被「已呈现全部」盖掉"
  );
  const incompleteAt = fn.indexOf("libraryLoadIncomplete");
  const allAt = fn.indexOf("已呈现全部");
  assert.ok(
    incompleteAt < allAt,
    "libraryLoadIncomplete 的判断必须排在「已呈现全部」前面"
  );
});

it("整库重载时要清掉上一次的补拉中断状态", () => {
  const fn = blockOf(eagle, "async function loadInitialLibraryData");
  assert.ok(fn, "找不到 loadInitialLibraryData");
  assert.ok(
    /libraryLoadIncomplete\s*=\s*null/.test(fn),
    "重新加载整库时必须把 libraryLoadIncomplete 清空，" +
      "否则上次的失败提示会一直挂着"
  );
});
