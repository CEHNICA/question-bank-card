"use strict";

// Three things a screenshot could not settle, pinned against the real source.
//
// 1. The paper header used to print "34 道题，32 题需要核查。" while the meter
//    right below it printed "2 / 34 已入库" and the filter tabs printed
//    "需要核查 32" — one set of numbers, three places. Only the states that
//    actually say something new may keep their sentence.
// 2. The colour legend node was written with a literal `hidden` attribute that
//    nothing ever removed, while app.js filled it on every render. It never
//    displayed. A live-looking node that stays invisible is worse than no node,
//    because the console says it has content.
// 3. "让 AI 重读这题" is the only re-read entry for PDF papers (photo papers get
//    a prominent button instead), and PDFs are the bulk of the bank — it shipped
//    with no hover text at all while its menu neighbour had one.
const assert = require("node:assert/strict");
const fs = require("node:fs");

const js = fs.readFileSync(require.resolve("./app.js"), "utf8");
const html = fs.readFileSync(require.resolve("./index.html"), "utf8");
const css = fs.readFileSync(require.resolve("./styles.css"), "utf8");

// --- 1. one set of numbers, one place -----------------------------------------
assert.doesNotMatch(js, /道题，\$\{c\.todo\} 题需要核查/,
  "the header must not restate the count the meter and the tabs already show");
assert.doesNotMatch(js, /道题，\$\{c\.approved\} 题已入库/,
  "the header must not restate the approved count either");
assert.match(js, /else if \(c\.todo \|\| c\.green\) \{\s*\n(?:[^\n]*\n)*?\s*statusText\.textContent = "";/,
  "both of those states now fall through to silence instead of a repeated number");

// Over-deleting is the other failure mode: the states that carry real
// information have to survive.
for (const [what, needle] of [
  ["失败/已停止", "statusText.textContent = paper.recoverable_pause"],
  ["题号乱序", "检测到题号重新开始或页面可能来自不同资料"],
  ["没有题卡", "原卷已保存，可以从原卷选取题目。"],
  ["全部处理完", "`全部 ${c.all} 题已处理完。`"]
]) {
  assert.ok(js.includes(needle), `the header still explains: ${what}`);
}

// --- 2. the invisible legend stays gone ----------------------------------------
assert.doesNotMatch(html, /meterLegend/, "no legend node with a permanent hidden attribute");
assert.doesNotMatch(js, /meterLegend/, "nothing fills a node the page cannot show");
assert.doesNotMatch(css, /\.meter-legend/, "no orphaned legend styles");
// The meter still labels its bar, so the colours are not left unlabelled.
assert.match(js, /bar\.setAttribute\("aria-label", segments/,
  "the meter bar still announces its segments");

// --- 3. the re-read entry explains itself --------------------------------------
// Extract button(...) by paren balancing so commas inside Chinese text and
// inside the arrow body do not throw the argument count off.
function callText(anchor) {
  const start = js.indexOf(anchor);
  assert.notEqual(start, -1, `找不到调用：${anchor}`);
  const open = js.indexOf("(", start);
  let depth = 0;
  let i = open;
  for (; i < js.length; i += 1) {
    if (js[i] === "(") depth += 1;
    else if (js[i] === ")") {
      depth -= 1;
      if (!depth) break;
    }
  }
  return js.slice(open, i + 1);
}

function argCount(text) {
  let depth = 0;
  let commas = 0;
  let quote = null;
  for (const ch of text.slice(1, -1)) {
    if (quote) {
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    else if ("([{".includes(ch)) depth += 1;
    else if (")]}".includes(ch)) depth -= 1;
    else if (ch === "," && depth === 0) commas += 1;
  }
  return commas + 1;
}

const reread = callText('button("让 AI 重读这题"');
assert.ok(argCount(reread) >= 4,
  "让 AI 重读这题 must carry hover text — it is the only re-read entry for PDF papers");
assert.match(reread, /全部片段/, "the hover text says what gets re-read");
assert.match(reread, /按 C/, "the hover text points at the narrower option, now the C shortcut");
assert.match(reread, /改过/,
  "the hover text warns that re-reading replaces text the teacher already edited");

// 1.26.33：「框选识读（纠错）」这个入口撤了。窄的那一路现在是快捷键 C ——
// 在原卷上框一块，读到的字直接进剪贴板，不填题卡。
assert.doesNotMatch(js, /button\("框选识读（纠错）"/, "框选识读（纠错）is no longer a menu entry");
assert.match(js, /case "c": if \(q\) \{ event\.preventDefault\(\); openClipboardRead\(q\); \} break;/,
  "the narrower read is now the C shortcut");

// The photo-paper path keeps its prominent, fully explained button.
const photoRead = callText('button(q.ocr_pending ? "AI 识读中……"');
assert.ok(argCount(photoRead) >= 4, "the photo-paper re-read button keeps its hover text");
assert.match(photoRead, /识读文字会显示在原图旁供你审核/, "and it still says where the result shows up");

console.log("review counts and re-read wording checks: OK");
