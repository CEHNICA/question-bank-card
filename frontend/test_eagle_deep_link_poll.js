"use strict";
// OneNote 回跳轮询不能无限跑。
//
// 原来 startDeepLinkPolling 用 setInterval(consumeDeepLinkOnce, 1200) 无限定时器，
// 页面开着就一直每 1.2 秒打一次 /api/deep-link —— 挂一天就是 7 万多个请求，
// 后端每次都要读一次 runtime 目录把深链文件查一遍。
//
// 要求：轮询要限时、拿到定位请求就收工、页面不在前台就别打扰后端。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("Eagle 的 OneNote 回跳轮询要有限", async (t) => {
  await t.test("1. 不能再用无限定时器轮询", () => {
    assert.ok(
      !/setInterval\(\s*consumeDeepLinkOnce/.test(html),
      "不能用 setInterval 无限定时器轮询 deep-link"
    );
  });

  await t.test("2. 必须有轮询窗口上限", () => {
    assert.ok(
      /DEEP_LINK_POLL_WINDOW_MS/.test(html),
      "必须定义轮询窗口上限 DEEP_LINK_POLL_WINDOW_MS"
    );
    assert.ok(
      /DEEP_LINK_POLL_WINDOW_MS[^;]*>\s*\d/.test(html) ||
        /elapsed\s*>\s*DEEP_LINK_POLL_WINDOW_MS/.test(html),
      "必须真的拿窗口上限做判断，而不是定义一个常量摆着"
    );
  });

  await t.test("3. 消费到定位请求后必须停止轮询", () => {
    const fn = html.match(/function startDeepLinkPolling\([\s\S]{0,1200}?\n {4}\}/);
    assert.ok(fn, "应能找到 startDeepLinkPolling");
    assert.ok(
      /consumed|if\s*\(.*\)\s*return/.test(fn[0]),
      "startDeepLinkPolling 必须判断本次是否消费到了定位请求"
    );
    assert.ok(
      /setTimeout/.test(fn[0]),
      "应改用 setTimeout 递归，以便随时收手"
    );
  });

  await t.test("4. consumeDeepLinkOnce 要回报有没有消费到", () => {
    const fn = html.match(/async function consumeDeepLinkOnce\([\s\S]{0,1200}?\n {4}\}/);
    assert.ok(fn, "应能找到 consumeDeepLinkOnce");
    assert.ok(
      /return\s+true/.test(fn[0]),
      "消费到定位请求时要 return true"
    );
    assert.ok(
      /return\s+false/.test(fn[0]),
      "没消费到时要 return false"
    );
  });

  await t.test("5. 页面不在前台时不要打扰后端", () => {
    const fn = html.match(/function startDeepLinkPolling\([\s\S]{0,1200}?\n {4}\}/);
    assert.ok(fn, "应能找到 startDeepLinkPolling");
    assert.ok(
      /document\.hidden/.test(fn[0]),
      "startDeepLinkPolling 必须看 document.hidden，页面在后台时暂停"
    );
  });

  await t.test("6. 轮询节奏要有分档：开头密、后面疏", () => {
    assert.ok(
      /DEEP_LINK_POLL_FAST_MS/.test(html) && /DEEP_LINK_POLL_SLOW_MS/.test(html),
      "应区分快慢两档轮询间隔"
    );
  });

  await t.test("7. 窗口被置前时要能再开一波回跳轮询", () => {
    assert.ok(
      /armDeepLinkPollingFromFocus/.test(html),
      "应有 armDeepLinkPollingFromFocus，供 visibility/focus 再开一波"
    );
    assert.ok(
      /visibilitychange/.test(html),
      "页面重新可见时应接住 OneNote 回跳"
    );
    assert.ok(
      /addEventListener\(\s*['"]focus['"]/.test(html),
      "窗口 focus 时应接住 OneNote 回跳"
    );
  });
});
