import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// 人机验证脚本是第三方资源，慢或者被墙时会一直挂着。
// 这套用例盯住两件事：表单永远不会走浏览器原生提交，提交处理一定先于脚本挂载绑定。
const source = readFileSync(new URL("../web/src/views/account.js", import.meta.url), "utf8");

test("登录/注册表单不走浏览器原生提交，密码不会被拼进 URL", () => {
  assert.match(
    source,
    /<form id="authForm"[^>]*onsubmit="return false"/,
    "authForm 必须带 onsubmit=\"return false\"，否则原生提交会把密码写进查询串",
  );
});

test("提交处理先于人机验证挂载绑定，脚本慢时点登录不会刷新页面", () => {
  const bind = source.indexOf('form.addEventListener("submit"');
  const mount = source.indexOf("await mountTurnstile(");
  assert.ok(bind >= 0, "没找到 submit 监听");
  assert.ok(mount >= 0, "没找到 mountTurnstile 调用");
  assert.ok(bind < mount, "submit 监听必须排在 await mountTurnstile 之前");
});

test("人机验证没就绪时提交被拦下并给出提示", () => {
  const bind = source.indexOf('form.addEventListener("submit"');
  const body = source.slice(bind, bind + 1200);
  assert.match(body, /if\s*\(!captcha\)/, "提交前要检查人机验证是否就绪");
  assert.match(body, /event\.preventDefault\(\)/, "提交事件必须先 preventDefault");
});
