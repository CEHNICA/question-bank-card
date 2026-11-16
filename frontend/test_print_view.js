// 第二十一轮守护测试：打印视图（print 媒体）。
//
// 背景：老师把组卷预览直接 Ctrl+P 印出来，纸上该有的（题干、选项、配图、答案）
// 齐不齐，不该有的（按钮、下拉、「调整」折叠块、提示条）有没有混进去。
// 实测（Playwright 真出 PDF + PyMuPDF 抠纸面文字）：
//   · 预览态打印：4 张纸 = 4 页，题干文字段 28 段全部在纸上，配图 1/1/3/1 张一张不少，
//     界面词 0 个混入 —— 这条主路径本来是干净的，别把它改坏。
//   · 没开预览直接 Ctrl+P：整页题库界面被原样印出来，实测 18 页，
//     纸上全是「加入试题篮」「查看出处」「收起目录」「按课本目录新建」「标签与详情」
//     这类界面字，一道题都印不成样 —— 这是真缺陷。
//
// 修法：不在预览态时，print 媒体下把题库界面收掉，只留一句指引（从试题篮→组卷预览印）。
//
// 这里的断言是**静态源码断言**：CI 里没有浏览器，跑不了真打印。
// 所以尺子必须自己会认规则 —— 第一条就是尺子自检（拿一定存在的规则去探，探不到就红），
// 免得看门狗看着在跑、其实断言恒真。
//
// 想自证看门狗有效，可以用环境变量喂旧版副本：
//   QB_LIB_CSS=旧版library.css node --test frontend/test_print_view.js

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const LIB_CSS = process.env.QB_LIB_CSS || path.join(ROOT, "frontend", "library.css");

// ── 迷你 CSS 解析器（与 test_zoom_layout.js 同一套做法）─────────────
// 注释用等长空格遮罩（直接删会让后面所有字符坐标前移，末尾判定会切错位置）。
function maskComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => " ".repeat(m.length));
}

function collectRules(css) {
  const rules = [];

  const scan = (block, media) => {
    let i = 0;
    while (i < block.length) {
      const brace = block.indexOf("{", i);
      if (brace < 0) break;
      const head = block.slice(i, brace).trim();
      let depth = 1;
      let j = brace + 1;
      while (j < block.length && depth > 0) {
        const c = block[j];
        if (c === "{") depth += 1;
        else if (c === "}") depth -= 1;
        j += 1;
      }
      const body = block.slice(brace + 1, j - 1);
      if (head) {
        if (head.startsWith("@media")) {
          scan(body, head.replace(/^@media\s*/, "").trim());
        } else if (!head.startsWith("@")) {
          const selectors = head.split(",").map((s) => s.trim()).filter(Boolean);
          rules.push({ media, selectors, body });
        }
      }
      i = j;
    }
  };

  scan(maskComments(css), null);
  return rules;
}

function decls(body) {
  const out = {};
  for (const part of body.split(";")) {
    const idx = part.indexOf(":");
    if (idx < 0) continue;
    const prop = part.slice(0, idx).trim().toLowerCase();
    const value = part.slice(idx + 1).trim();
    if (prop) out[prop] = value;
  }
  return out;
}

function rulesFor(rules, matcher) {
  return rules.filter((r) => r.selectors.some((s) => matcher(s)));
}

function merged(rules, matcher) {
  const out = {};
  for (const r of rulesFor(rules, matcher)) Object.assign(out, decls(r.body));
  return out;
}

function inMedia(rules, mediaNeedle, matcher) {
  return rules.filter(
    (r) => r.media && r.media.includes(mediaNeedle) && r.selectors.some((s) => matcher(s)),
  );
}

const libraryCss = fs.readFileSync(LIB_CSS, "utf8");
const libRules = collectRules(libraryCss);

// 打印媒体块（@media print）
const printRules = libRules.filter((r) => r.media && r.media.includes("print"));

test("尺子自检：解析器认得已知规则，且不是恒真", () => {
  // .print-sheet 在 library.css 里一定存在（组卷预览的壳）
  const hit = rulesFor(libRules, (s) => s === ".print-sheet");
  assert.ok(hit.length > 0, "解析器没认出 .print-sheet，尺子坏了");
  // 合并出来的声明必须真的有值（防「匹配到了但取不到属性」的恒真）
  const decl = merged(libRules, (s) => s === ".print-sheet");
  assert.ok(decl.position !== undefined, "合并声明取不到属性，尺子坏了");
  // 打印媒体块必须扫得到（扫不到 → 后面的断言会假红或恒真）
  assert.ok(printRules.length > 0, "解析器没认出 @media print 里的规则，尺子坏了");
  // 媒体查询块里得能找到「预览态隐藏其它东西」这条老规则
  const old = printRules.filter((r) =>
    r.selectors.some((s) => s.includes("body.printing > *:not(.print-sheet)")),
  );
  assert.ok(old.length > 0, "打印媒体里找不到 body.printing 那条老规则，尺子坏了");
});

test("D1 不在预览态就不印题库界面（原来会印出 18 页界面垃圾）", () => {
  const blocks = inMedia(libRules, "print", (s) =>
    s.includes("body:not(.printing) > main.library-main"),
  );
  assert.ok(blocks.length > 0, "打印媒体里找不到 body:not(.printing) > main.library-main 这条规则");
  const decl = merged(blocks, (s) => s.includes("body:not(.printing) > main.library-main"));
  assert.strictEqual(
    decl.display,
    "none !important",
    "非预览态下题库界面没有在打印时被收起来",
  );
});

test("D2 不在预览态打印时给一句指引，告诉他从哪儿印", () => {
  const blocks = inMedia(libRules, "print", (s) =>
    s.includes("body:not(.printing)::after"),
  );
  assert.ok(blocks.length > 0, "打印媒体里找不到 body:not(.printing)::after 这条规则");
  const decl = merged(blocks, (s) => s.includes("body:not(.printing)::after"));
  assert.ok(decl.content, "指引语没有内容");
  const text = String(decl.content);
  assert.ok(text.includes("组卷预览"), "指引语没告诉老师去组卷预览里印");
  assert.ok(text.includes("试题篮"), "指引语没告诉老师从试题篮进组卷预览");
  assert.strictEqual(decl.display, "block", "指引语的 display 不是 block");
});

test("D3 主路径没被误伤：卷子、纸张、预览壳都不许在打印时被藏起来", () => {
  const victims = [".print-paper", ".exam-page", "#printSheet", ".print-workspace"];
  for (const sel of victims) {
    const blocks = inMedia(libRules, "print", (s) => s === sel);
    if (!blocks.length) continue;
    const decl = merged(blocks, (s) => s === sel);
    assert.notStrictEqual(
      decl.display,
      "none",
      `打印时 ${sel} 被藏起来了 —— 卷子会印成空白`,
    );
  }
});

test("D4 打印媒体里原有的隐藏清单还在（别把块改坏）", () => {
  // 注意：隐藏清单是一条逗号分隔的长选择器，.site-drawer 与 .question-inspector
  // 是其中两个「独立的选择器项」，不能拿单个选择器项去做「同时包含两者」的判断。
  const hideList = printRules.filter(
    (r) =>
      r.selectors.some((s) => s.includes(".site-drawer")) &&
      r.selectors.some((s) => s.includes(".question-inspector")),
  );
  assert.ok(hideList.length > 0, "打印媒体里的隐藏清单没了，尺子指错了地方");
  const decl = {};
  for (const r of hideList) Object.assign(decl, decls(r.body));
  assert.strictEqual(decl.display, "none !important", "隐藏清单不再生效");
});

test("D5 打印媒体里不许把 body 整体藏起来（那样连指引都没了）", () => {
  const blocks = inMedia(libRules, "print", (s) => s === "body");
  for (const b of blocks) {
    const decl = decls(b.body);
    assert.notStrictEqual(decl.display, "none", "打印时整个 body 被藏起来了");
  }
});
