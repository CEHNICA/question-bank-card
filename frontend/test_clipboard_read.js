"use strict";

// 1.26.33：框选识读到剪贴板。
// 「更多」里撤掉了「查看识读记录」和「框选识读（纠错）」；窄的那一路改成快捷键 C ——
// 在任何要改字的地方按一下，在原卷上框一块，读到的字（印刷的和手写的都认）直接进剪贴板。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const app = require("./app.js");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const library = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
const help = fs.readFileSync(path.join(__dirname, "shortcut-help.js"), "utf8");

// ---- 「更多」菜单：两个撤了，两个留着
assert.doesNotMatch(js, /button\("查看识读记录", "quiet small"/, "「更多」里不再有查看识读记录");
assert.doesNotMatch(js, /button\("框选识读（纠错）"/, "「更多」里不再有框选识读（纠错）");
assert.match(js, /button\("修改题目来源", "quiet small"/, "修改题目来源留着");
assert.match(js, /button\("让 AI 重读这题", "quiet small"/, "让 AI 重读这题留着");
// 识读记录还在「其他读法还有不同内容」里 —— 那是它唯一该出现的地方。
assert.match(js, /button\("查看识读记录", "small", \(\) => toggleReadsNear\(panel, q\)\)/,
  "识读记录留在差异说明里");

// ---- 快捷键 C：审核页、放大对照页、题库页
assert.match(js, /case "c": if \(q\) \{ event\.preventDefault\(\); openClipboardRead\(q\); \} break;/,
  "审核页按 C 打开框选识读");
assert.match(js, /key\.toLowerCase\(\) === "c"[\s\S]{0,400}openClipboardRead\(q\)/,
  "放大对照页按 C 打开框选识读");
assert.match(library, /event\.key\.toLowerCase\(\) === "c"/, "题库页认 C");
assert.match(library, /draftLink\(current, "&read=clipboard"\)/, "题库页按 C 带着这道题跳到原卷");
assert.match(js, /params\.get\("read"\) === "clipboard"/, "从题库跳过来会直接打开框选识读");
assert.match(help, /"框选识读到剪贴板", "C"/, "快捷键说明里查得到这一条");

// ---- 这一路只抄剪贴板，不填题卡
assert.match(js, /const target = "clipboard";/, "框选识读固定走剪贴板这一路");
assert.doesNotMatch(html, /id="readTargetSelect"/, "不再让老师选填到题干还是选项");
assert.match(js, /QBClipboard\.copyText\(read\.text\)/, "读到的字自动抄进剪贴板");
// 自动抄的那一下，挂在数据上，不挂在画卡片上：老师正在改字时卡片不会重画。
const autoCopy = js.slice(js.indexOf("function autoCopyClipboardReads("), js.indexOf("function clipboardCopyBlock("));
assert.match(autoCopy, /QBClipboard\.copyText\(read\.text\)/, "结果一出现就自动抄");
assert.match(autoCopy, /read\.target !== "clipboard"/, "只有剪贴板这一路才自动抄");
assert.match(js, /复制到剪贴板/, "抄不进去也有一个按钮让老师自己按");

// ---- 复制这件事本身
(async () => {
  const written = [];
  const direct = await app.QBClipboard.copyText("读出来的字", {
    navigator: { clipboard: { writeText: async (value) => { written.push(value); } } },
    document: null
  });
  assert.equal(direct.ok, true, "浏览器接口能用时就是它");
  assert.deepEqual(written, ["读出来的字"]);

  // 浏览器不给用时，退回到隐藏文本框那条老路。
  const created = [];
  const doc = {
    body: { append: (node) => created.push(node) },
    createElement: () => ({ value: "", style: {}, setAttribute() {}, focus() {}, select() {}, remove() {} }),
    execCommand: (name) => name === "copy"
  };
  const fallback = await app.QBClipboard.copyText("手写的字", { navigator: {}, document: doc });
  assert.equal(fallback.ok, true, "退回的老路也要能用");
  assert.equal(fallback.how, "fallback");
  assert.equal(created.length, 1);
  assert.equal(created[0].value, "手写的字");

  // 两条路都不通就说清楚，不能装作复制成功了。
  const denied = await app.QBClipboard.copyText("字", {
    navigator: { clipboard: { writeText: async () => { throw new Error("not allowed"); } } },
    document: { body: { append() {} }, createElement: () => ({ style: {}, setAttribute() {}, focus() {}, select() {}, remove() {} }), execCommand: () => false }
  });
  assert.equal(denied.ok, false, "复制不了就说复制不了");
  assert.match(denied.error, /复制/, "并且说清楚下一步怎么做");

  const empty = await app.QBClipboard.copyText("", { navigator: {}, document: null });
  assert.equal(empty.ok, false, "没有字就不假装复制");

  console.log("clipboard read checks: OK");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
