// 第二十六轮：行内公式不许从中间被断开。
//
// 这轮体检在题库页实测出一条真缺陷：
//   KaTeX 把一段公式拆成多个 .katex-base（"AE=3DE" → "AE=" + "3DE"），
//   .katex-base 自己 white-space:nowrap，可两个 base **之间**留着断行机会，
//   而 .katex-html 从 .qb-stem-body 继承了 pre-wrap ——
//   公式于是被排成两行："AE=" 挂在行尾，"3DE" 掉到下一行行首。
//   实测：1440px 下 40 题里有 1 处（$AE=3DE$），390px 下有 3 处
//   （$CE\perp AB$、$PM \perp AD$、$DE+BF=EF$）；窄屏还会连累出「行首标点」。
//
// 修法在 qb-render.js：行内公式整段锁成 nowrap；display 公式不动
//   （它自带 overflow-x:auto，该滚就滚）。打印流另有规则豁免（见 library.css）。
//
// 守护测试是静态源码断言；实测那部分由 _audit_zh13.py / _verify26b.py 负责。
// 用 QB_RENDER_JS / QB_LIB_CSS 喂旧版本可以自证看门狗有效。

const path = require("node:path");
const fs = require("node:fs");
const test = require("node:test");
const assert = require("node:assert/strict");

const ROOT = path.resolve(__dirname, "..");
function read(envName, rel) {
  return fs.readFileSync(process.env[envName] || path.join(ROOT, rel), "utf8");
}
const renderJs = read("QB_RENDER_JS", "frontend/qb-render.js");
const libCss = read("QB_LIB_CSS", "frontend/library.css");

// 渲染数学段的那段代码（从 `if (segment.type === "math")` 起）。
function mathSegmentBlock(src) {
  const at = src.indexOf('if (segment.type === "math")');
  if (at < 0) return null;
  return src.slice(at, at + 2400);
}

// 所有 `span.style.whiteSpace = ...` 的下标（不看值，只看有没有、守卫在不在）。
function nowrapAssignments(src) {
  const out = [];
  const re = /span\.style\.whiteSpace\s*=\s*"[^"]*"/g;
  let m;
  while ((m = re.exec(src))) out.push(m.index);
  return out;
}

// library.css 里所有声明了 .katex-html / .katex 的 white-space 的规则。
function katexWhiteSpaceRules(css) {
  const out = [];
  const re = /([^{}]*\.katex[^{}]*)\{([^{}]*white-space\s*:[^{}]*)\}/g;
  let m;
  while ((m = re.exec(css))) {
    if (!/white-space/.test(m[2])) continue;
    out.push({ selector: m[1].trim(), body: m[2].trim() });
  }
  return out;
}

// ── 0. 尺子自检 ────────────────────────────────────────────────────
test("尺子自检：找得到渲染数学段的那段代码", () => {
  const block = mathSegmentBlock(renderJs);
  assert.ok(block, "尺子自检：qb-render.js 里找不到 `if (segment.type === \"math\")`（改名了？这条断言要重写）");
  assert.ok(/segment\.display/.test(block),
    "尺子自检：数学段代码里没有 segment.display，行内/展示公式分不开，尺子本身坏了");
  // 反向自检：查找器对不存在的东西必须返回空。
  assert.equal(mathSegmentBlock(renderJs.replace('if (segment.type === "math")', 'if (segment.type === "nope")')), null,
    "尺子自检：查找器对不存在的分支也返回了东西");
});

// ── 1. 行内公式必须整体 nowrap ──────────────────────────────────────
test("排版：行内公式整段锁成 nowrap，不许从中间断开", () => {
  const block = mathSegmentBlock(renderJs);
  assert.ok(block, "尺子自检：找不到渲染数学段的代码");
  assert.match(block, /whiteSpace\s*=\s*"nowrap"/,
    "行内公式没有锁成 nowrap：KaTeX 会把公式拆成多个 .katex-base，"
    + "base 之间有断行机会，公式被排成两行（实测 $AE=3DE$ 被劈成 \"AE=\" 和 \"3DE\"）");
});

// ── 2. display 公式不能被一起锁死 ──────────────────────────────────
test("排版：只有行内公式锁 nowrap，展示公式要保持能滚", () => {
  const positions = nowrapAssignments(renderJs);
  assert.ok(positions.length > 0, "尺子自检：整个文件里找不到 span.style.whiteSpace = \"nowrap\"");
  const unguarded = positions.filter((at) => {
    const before = renderJs.slice(Math.max(0, at - 240), at);
    return !/!segment\.display/.test(before);
  });
  assert.equal(unguarded.length, 0,
    "有一处 nowrap 没加 `!segment.display` 判断：展示公式（.qb-math.is-display）"
    + "本来靠 overflow-x:auto 横向滚动，一刀切 nowrap 会让它溢出纸面");
  // 展示公式的样式必须还在（别把滚动能力一起删了）。
  assert.match(libCss + renderJs, /is-display/,
    "尺子自检：找不到 is-display，尺子对展示公式的认知可能已经过时");
});

// ── 3. 打印流豁免必须还在 ──────────────────────────────────────────
test("排版：打印流仍按原来的方式换行（豁免规则还在）", () => {
  assert.match(libCss, /\.print-paper[^{]*\.katex-html[^{]*\{[^}]*white-space\s*:\s*normal/,
    "打印流不再豁免行内公式的 nowrap：纸面宽度有限，公式宁可换行也不能溢出纸外");
  assert.match(libCss, /\.print-flow[^{]*\.katex[^{]*\{[^}]*white-space\s*:\s*normal/,
    "打印流不再豁免行内公式的 nowrap");
});

// ── 4. 不能一刀切锁死 .katex-html ──────────────────────────────────
test("排版：不得全局锁死 .katex-html 的换行", () => {
  const offenders = katexWhiteSpaceRules(libCss).filter((r) =>
    /white-space\s*:\s*nowrap/.test(r.body)
    && !/print-(paper|flow)/.test(r.selector));
  assert.equal(offenders.length, 0,
    "这些规则会把 .katex-html 一刀切锁成 nowrap（展示公式、分式、矩阵都会被误伤）：\n  "
    + offenders.map((r) => r.selector).join("\n  "));
});
