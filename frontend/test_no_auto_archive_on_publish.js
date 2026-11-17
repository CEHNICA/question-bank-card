"use strict";
// 入库完成不得自动归档：归档只走人点的「归档任务」。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

function bodyOf(source, signature) {
  const start = source.indexOf(signature);
  if (start < 0) return "";
  let i = start + signature.length;
  let depth = 0;
  let begun = false;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "{") {
      depth += 1;
      begun = true;
    } else if (ch === "}") {
      depth -= 1;
      if (begun && depth === 0) return source.slice(start, i + 1);
    }
  }
  return "";
}

test("入库完成不自动归档，交给人手归档", async (t) => {
  await t.test("1. 自动归档函数必须消失", () => {
    assert.doesNotMatch(js, /function retireCompletedPaper\(/);
    assert.doesNotMatch(js, /function sweepCompletedEntryPapers\(/);
    assert.doesNotMatch(js, /function retireCurrentPaperIfComplete\(/);
    assert.doesNotMatch(js, /retiringPaperIds/);
  });

  await t.test("2. 通过入库路径不得顺手归档", () => {
    const approve = bodyOf(js, "async function approveQuestion");
    assert.ok(approve, "应能找到 approveQuestion");
    assert.doesNotMatch(approve, /\/archive|retireCompleted/);
    const batch = bodyOf(js, "async function approveAllGreen");
    assert.ok(batch, "应能找到 approveAllGreen");
    assert.doesNotMatch(batch, /\/archive|retireCompleted/);
  });

  await t.test("3. 完成横幅只提供人手归档", () => {
    const banner = bodyOf(js, "function renderDoneBanner");
    assert.ok(banner, "应能找到 renderDoneBanner");
    assert.match(banner, /归档任务/);
    assert.match(banner, /archivePaper\(\)/);
    assert.doesNotMatch(banner, /正在从录入列表收起|立刻收起/);
  });

  await t.test("4. 列表标题不再承诺自动收起", () => {
    assert.match(html, /自行归档收起/);
    assert.doesNotMatch(html, /全部入库后会自动收起/);
  });

  await t.test("5. 刷新列表不再清扫已完成卷", () => {
    const load = bodyOf(js, "async function loadPapers");
    assert.ok(load, "应能找到 loadPapers");
    assert.doesNotMatch(load, /sweepCompletedEntryPapers/);
  });
});
