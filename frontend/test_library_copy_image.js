"use strict";

// 1.26.48：题卡上的「复制题目图片」。
//
// 这几条盯的是真东西，不是「源码里有没有这句话」：
// 1. 按钮真在题卡的「更多」菜单里（挂上才谈得上能用）。
// 2. 按下之后确实去调后端那一个接口，方法、请求头、内容都对。
// 3. 出图要一两秒，这段时间按钮要写「正在生成…」——
//    不说清楚，老师会以为按钮坏了。
// 4. 成功和失败都要给一句话，不能闷声失败。
// 5. 按钮用完要恢复原样，不能永远停在「正在生成…」。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const js = fs.readFileSync(require.resolve("./library.js"), "utf8");

// --- 源码里的挂接点 ---------------------------------------------------------
assert.match(js, /const copyImage = node\("button", "button button-quiet button-small", "复制题目图片"\);/,
  "题卡上要有「复制题目图片」这个按钮");
assert.match(js, /menu\.append\(editQuestion, editAnswer, review, copyImage, withdraw, \.\.\.jobButtons\(item\)\);/,
  "按钮要挂在题卡的「更多」菜单里");
assert.match(js, /copyImage\.addEventListener\("click", \(\) => \{ copyQuestionImage\(item, copyImage\); \}\);/,
  "按钮点了要调 copyQuestionImage");
assert.doesNotMatch(js, /closeMenu\(\); copyQuestionImage\(/,
  "点了不能把菜单关掉 —— 菜单一关，按钮上那行「正在生成…」就看不见了");

// 悬停说明要说清复制的是什么、能干什么。
assert.match(js, /copyImage\.title = "把这道题做成图片放进剪贴板。/,
  "按钮要有悬停说明");
assert.ok(js.includes("粘到 OneNote 里能分开拖动位置"),
  "悬停说明要说明配图能分开拖动");

// --- 真跑一遍按钮的逻辑 -----------------------------------------------------
const source = js.slice(js.indexOf("  async function copyQuestionImage(item, button) {"));
const body = source.slice(0, source.indexOf("\n  }\n") + 4);

const calls = [];
const toasts = [];
const sandbox = {
  toast: (message, kind) => { toasts.push({ message, kind }); },
  fetch: async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      json: async () => ({ images: 2, message: "已复制：题目 1 张、配图 1 张。" })
    };
  }
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(body, sandbox);

(async () => {
  const button = { disabled: false, textContent: "复制题目图片" };
  await sandbox.copyQuestionImage({ id: "abc-123" }, button);

  assert.equal(calls.length, 1, "点一次只该发一次请求");
  // 先说「正在生成」，成了再说「已复制」—— 两条提示都要有。
  assert.ok(toasts.length >= 2, "要有「正在生成」和「已复制」两条提示");
  assert.match(toasts[0].message, /正在生成/);
  assert.match(toasts[toasts.length - 1].message, /已复制/);
  assert.equal(calls[0].url, "/api/library/copy-image");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.headers["X-QB-Request"], "1");
  assert.deepEqual(JSON.parse(calls[0].options.body), { publication_id: "abc-123" });

  // 「正在生成」一条，「已复制」一条。
  assert.equal(toasts.length, 2, "出图期间一条提示，成了再一条");
  assert.match(toasts[0].message, /正在生成/);
  assert.match(toasts[1].message, /题目 1 张/);
  assert.equal(toasts[1].kind, "success");

  assert.equal(button.disabled, false, "按钮要恢复可点");
  assert.equal(button.textContent, "复制题目图片", "按钮的字要恢复原样");

  // --- 失败也要说人话 -------------------------------------------------------
  calls.length = 0;
  toasts.length = 0;
  sandbox.fetch = async () => ({
    ok: false,
    status: 409,
    json: async () => ({ error: "另一道题正在出图，请稍等一下再试" })
  });
  const failing = { disabled: false, textContent: "复制题目图片" };
  await sandbox.copyQuestionImage({ id: "abc-123" }, failing);
  assert.ok(toasts.length >= 2, "失败也要给一句话");
  assert.match(toasts[toasts.length - 1].message, /另一道题正在出图/);
  assert.equal(toasts[toasts.length - 1].kind, "error");
  assert.equal(failing.disabled, false, "失败后按钮也要恢复可点");
  assert.equal(failing.textContent, "复制题目图片");

  // --- 出图期间按钮要说明在做什么 ---------------------------------------------
  calls.length = 0;
  toasts.length = 0;
  let seen = null;
  sandbox.fetch = async () => {
    seen = { disabled: busy.disabled, text: busy.textContent };
    return { ok: true, status: 200, json: async () => ({ images: 1, message: "好了" }) };
  };
  const busy = { disabled: false, textContent: "复制题目图片" };
  await sandbox.copyQuestionImage({ id: "x" }, busy);
  assert.ok(seen, "请求发出去了");
  assert.equal(seen.disabled, true, "出图期间按钮不能重复点");
  assert.equal(seen.text, "正在生成…", "出图期间按钮要说明在做什么");
  // 光靠按钮上的字不够：菜单可能被点掉、老师可能在看别处。
  // 必须另有一条提示条，明说在做什么。
  assert.ok(toasts.length >= 1, "出图期间要有提示条");
  assert.match(toasts[0].message, /正在生成/);
  assert.match(toasts[0].message, /秒/, "提示条要说个大概多久");

  console.log("test_library_copy_image: 全部通过");
})();
