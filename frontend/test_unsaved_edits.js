"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createEditGuard, protectBeforeUnload } = require("./app.js");

function editor(guard, id, initial) {
  const form = { values: structuredClone(initial), discarded: 0, resumed: 0 };
  guard.track(id, () => form.values, () => {
    form.discarded += 1;
    guard.release(id);
  }, () => { form.resumed += 1; });
  return form;
}

(async () => {
  const guard = createEditGuard();
  const first = editor(guard, 11, { stem: "原题", answer: "A", origin: "原卷", options: { A: "1" } });
  let confirmations = 0;
  assert.equal(guard.hasPendingWork(), false, "仅打开改字表单，不应阻止离开");
  first.values.stem = "临时修改";
  assert.equal(guard.hasPendingWork(), true);
  first.values.stem = "原题";
  assert.equal(guard.hasPendingWork(), false, "改回原文应取消未保存警告");
  first.values.origin = "另一题源";
  assert.equal(guard.hasPendingWork(), true, "题源等其他字段也必须纳入保护");

  assert.equal(await guard.discard(async (count) => {
    confirmations += 1;
    assert.equal(count, 1);
    return false;
  }), false);
  assert.equal(first.discarded, 0, "选择继续编辑后，不关闭或重建表单");
  assert.equal(first.values.origin, "另一题源");
  assert.equal(first.resumed, 1);
  assert.equal(guard.hasPendingWork(), true);

  // A cancel on one card cannot discard another card's unsaved edits.
  const second = editor(guard, 12, { stem: "第二题", options: {} });
  second.values.options.B = "新增选项";
  assert.equal(await guard.discard(async () => true, [11]), true);
  assert.equal(first.discarded, 1);
  assert.equal(second.discarded, 0);
  assert.equal(guard.hasPendingWork(), true);

  const unload = { prevented: 0, returnValue: undefined, preventDefault() { this.prevented += 1; } };
  assert.equal(protectBeforeUnload(unload, guard), true, "刷新/关页时使用浏览器的未保存提示");
  assert.equal(unload.prevented, 1);
  assert.equal(unload.returnValue, "");

  // While the save request is unresolved, cancelling or switching papers must
  // not remove the editor even if the form had no textual change.
  guard.setSaving(12, true);
  assert.equal(await guard.discard(async () => { throw new Error("不应在保存中询问丢弃"); }), false);
  assert.equal(second.discarded, 0);
  assert.equal(guard.hasSaving(), true);
  guard.setSaving(12, false);

  // A second navigation click cannot piggyback on the first confirmation.
  let answer;
  const pending = guard.discard(() => new Promise((resolve) => { answer = resolve; }));
  assert.equal(await guard.discard(async () => true), false);
  assert.equal(second.discarded, 0);
  answer(false);
  assert.equal(await pending, false);
  assert.equal(second.discarded, 0);

  assert.equal(await guard.discard(async (count) => {
    assert.equal(count, 1);
    return true;
  }), true);
  assert.equal(second.discarded, 1);
  assert.equal(guard.hasPendingWork(), false);
  const cleanUnload = { preventDefault() { throw new Error("没有未保存改动，不应拦截关闭"); } };
  assert.equal(protectBeforeUnload(cleanUnload, guard), false);

  const clean = editor(guard, 13, { stem: "未修改" });
  assert.equal(await guard.discard(async () => { confirmations += 1; return true; }), true);
  assert.equal(confirmations, 1, "未改动的表单退出无需确认");
  assert.equal(clean.discarded, 1);

  const savingClean = editor(guard, 14, { stem: "没有改字但已点保存" });
  guard.setSaving(14, true);
  assert.equal(guard.hasPendingWork(), true, "保存请求尚未完成，即使文本未改也要保护离开");
  guard.release(14);
  assert.equal(guard.hasPendingWork(), false, "成功保存并关闭后应清除关闭提示");
  assert.equal(savingClean.discarded, 0, "释放保存状态不应调用丢弃或额外写入");

  const multiple = createEditGuard();
  const a = editor(multiple, 1, { stem: "甲" });
  const b = editor(multiple, 2, { stem: "乙" });
  a.values.stem = "甲改";
  b.values.stem = "乙改";
  assert.equal(await multiple.discard(async (count) => {
    assert.equal(count, 2, "离开试卷应提示全部未保存题数");
    return false;
  }), false);
  assert.equal(a.discarded + b.discarded, 0);
  assert.equal(a.values.stem, "甲改");
  assert.equal(b.values.stem, "乙改");

  // Verify the real page wires all lossy paths to the guard. Browser tests
  // exercise the dialog; these checks catch an accidentally bypassed route.
  const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  assert.match(js, /async function selectPaper\(id\) \{\s*if \(state\.paperId !== id && !\(await discardEdits\(\)\)\) return;/);
  assert.match(js, /window\.addEventListener\("beforeunload", \(event\) => \{\s*QBEdits\.protectBeforeUnload\(event, editGuard\)/);
  assert.match(js, /async function leaveFor\(url\) \{\s*if \(!\(await prepareSettingsLeave\(\)\) \|\| !\(await discardEdits\(\)\)\) return false;/);
  assert.match(js, /const cancel = button\("取消", "", \(\) => discardEdits\(\[q\.id\]\)\)/);
  assert.match(js, /event\.stopPropagation\(\); discardEdits\(\[q\.id\]\)/);
  assert.match(js, /label: "去题库看看", onClick: \(\) => leaveFor\("\/library"\)/);
  for (const name of ["deletePaper", "archivePaper"]) {
    const body = js.slice(js.indexOf(`async function ${name}()`), js.indexOf("// ----------------------------------------------------------------", js.indexOf(`async function ${name}()`)));
    assert.ok(body.indexOf("await discardEdits()") < body.indexOf("await api("), `${name} 必须在操作后端前检查未保存改动`);
  }
  console.log("unsaved edit protection checks: OK");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
