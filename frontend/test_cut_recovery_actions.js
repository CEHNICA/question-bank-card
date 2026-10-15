"use strict";

// 自动切题没切出题、但云端结果还留着的时候，最醒目的那个按钮必须是真的
// 能救回来的那一个，而不是把人推向手工框题；同一句话也不能在两个地方各写一遍。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const start = js.indexOf('    const error = $("paperError");');
const end = js.indexOf("    renderMeter(c);", start);
assert.ok(start > 0 && end > start, "the error row block is where the test expects it");
const source = js.slice(start, end).replace(/^ {4}/gm, "");

const el = (tag, className, ...children) => ({
  tag, className, children,
  append: (...kids) => children.push(...kids),
});
const button = (label, kind, onClick) => ({ label, kind, onClick });

function renderErrorRow(paper) {
  const written = [];
  const $ = (id) => ({ id, hidden: false, classList: { toggle() {} },
    replaceChildren: (...kids) => written.push(...kids) });
  const run = new Function("$", "el", "button", "paper", "QBProgress",
    "deletePaper", "switchToManual", "retryPaper", "continueAiCut", source);
  run($, el, button, paper, {
      canContinueAiCut: (p) => !p.archived && !p.demo
        && (["manual", "native"].includes(p.parse_mode) && ["ready", "failed"].includes(p.status)
          || (p.parse_mode === "mineru" && p.status === "failed")),
    }, () => {}, () => {}, () => {}, () => {});
  return { text: (written.find((n) => n.tag === "span" && n.children?.length === 1) || {}).children?.[0],
    buttons: written.flatMap((n) => n.children || []).filter((n) => n && n.label) };
}

const failedWithAParse = {
  id: "paper", kind: "pdf", status: "failed", parse_mode: "mineru", material_type: "exam",
  recoverable_pause: false, error: "自动切题没有切出任何题目。",
  processing_plan: { fallback_reason: "本地文字层没有可靠题卡，按本次授权尝试已配置的 MinerU。" },
};

// A service outage failed before cutting, so it says what actually happened.
const failedWithoutAParse = { ...failedWithAParse, error: "云端服务不可用。" };

const quotaPause = { ...failedWithAParse, recoverable_pause: true, error: "识读额度已用完。" };

(async () => {
  // 1. 云端结果还在：最显眼的按钮是“继续 AI 切题”，它复用已存的解析结果。
  const recoverable = renderErrorRow(failedWithAParse);
  assert.equal(recoverable.buttons[0].label, "继续 AI 切题",
    "the action that actually recovers the paper must be the prominent one");
  assert.equal(recoverable.buttons[0].kind, "small primary");
  assert.ok(!recoverable.buttons.some((b) => b.label === "重试自动处理"),
    "re-uploading the original would spend cloud quota a second time for nothing");
  assert.ok(recoverable.buttons.some((b) => b.label === "准备原卷并切题"), "manual cutting stays available");

  // 2. 错误行说的是“本地为什么没切出来”，不是把“处理失败”再抄一遍。
  assert.notEqual(recoverable.text, "自动切题没有切出任何题目。");
  assert.match(recoverable.text, /MinerU/);

  // 3. 别的失败原样说自己的原因，不套用切题那套按钮，也不换文案。
  const plain = renderErrorRow(failedWithoutAParse);
  assert.equal(plain.text, "云端服务不可用。");
  assert.equal(plain.buttons[0].label, "继续整理");
  assert.equal(plain.buttons[0].kind, "small primary");

  // 4. 与切题无关的失败（额度用完）同样不套用这套按钮。
  const quota = renderErrorRow(quotaPause);
  assert.equal(quota.text, "识读额度已用完。");
  assert.equal(quota.buttons[0].label, "继续整理");

  console.log("cut recovery actions ok");
})();
