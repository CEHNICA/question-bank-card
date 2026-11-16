// 第二十三轮：减弱动效（prefers-reduced-motion）。
//
// 这轮体检在题库页实测出四条缺陷：
//   1. 「定位题目」的高亮从来没显示过 —— keyframes 写的是 box-shadow，
//      而 eagle-filter-bar.css 的 .library-card 带着 !important，
//      属于「作者 !important」层，优先级高过 CSS 动画层，动画层一次都没赢过。
//   2. JS 的三处平滑滚动完全不看减弱动效，reduce 与否的滚动轨迹一模一样。
//   3. 无限动画（pulse）在减弱动效下只被压成 0.01ms 周期，
//      全局那条规则只压 animation-duration，不压 animation-iteration-count。
//   4. 卡片过渡在减弱动效下仍是 0.15s —— 同一条 !important 特异性更高。
//
// 守护测试是静态源码断言；实测那部分由 _audit_motion*.py 负责。
// 用 QB_LIB_CSS / QB_FILTER_CSS / QB_LIB_JS 喂旧版本可以自证看门狗有效。

const path = require("node:path");
const fs = require("node:fs");
const test = require("node:test");
const assert = require("node:assert/strict");

const ROOT = path.resolve(__dirname, "..");
function read(envName, rel) {
  return fs.readFileSync(process.env[envName] || path.join(ROOT, rel), "utf8");
}
const libCss = read("QB_LIB_CSS", "frontend/library.css");
const filterCss = read("QB_FILTER_CSS", "frontend/eagle-filter-bar.css");
const libJs = read("QB_LIB_JS", "frontend/library.js");

// 抓出一个 CSS 块的内容（配平花括号，不靠正则猜）。
function blockAt(src, openIndex) {
  let depth = 0;
  for (let i = openIndex; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(openIndex, i + 1);
    }
  }
  return "";
}

// 所有 prefers-reduced-motion 媒体块。
function reduceBlocks(css) {
  const out = [];
  const re = /@media \(prefers-reduced-motion: reduce\) \{/g;
  let m;
  while ((m = re.exec(css))) {
    out.push(blockAt(css, m.index + m[0].length - 1));
  }
  return out;
}

// 抓出函数调用的实参块：name({ ... }) → 返回 {...}
function callArgs(src, name) {
  const out = [];
  const re = new RegExp("\\b" + name + "\\(\\s*\\{", "g");
  let m;
  while ((m = re.exec(src))) {
    out.push(blockAt(src, m.index + m[0].length - 1));
  }
  return out;
}

// ── 1. JS 的平滑滚动必须看减弱动效 ────────────────────────────────
test("减弱动效：带 behavior 的滚动都要能切成瞬时", () => {
  const blocks = [...callArgs(libJs, "scrollIntoView"), ...callArgs(libJs, "scrollTo")]
    .filter((b) => b.includes("behavior:"));
  // 尺子自检：写法变了这里要跟着改，不然下面的断言会变成恒真。
  assert.ok(blocks.length >= 3,
    "尺子自检：带 behavior 的滚动调用只剩 " + blocks.length + " 个（至少要有定位题目、恢复焦点、回到顶部三处）");

  const noCheck = blocks.filter((b) => !/prefers-reduced-motion/.test(b));
  assert.equal(noCheck.length, 0,
    "这处滚动不看减弱动效，系统开了「减少动态效果」它照样慢慢滑：" + noCheck[0]);

  const halfBranch = blocks.filter((b) => !/"auto"/.test(b) || !/"smooth"/.test(b));
  assert.equal(halfBranch.length, 0,
    "这处滚动两个分支不全（要同时有 auto 和 smooth）：" + halfBranch[0]);
});

// ── 2. 定位高亮不能用 box-shadow ──────────────────────────────────
test("定位高亮改用 outline：box-shadow 会被作者 !important 压死", () => {
  const rule = /\.library-card\.locate-flash \{[^}]*\}/.exec(libCss);
  assert.ok(rule, "尺子自检：找不到 .library-card.locate-flash 规则");
  assert.match(rule[0], /animation:\s*library-locate-flash/,
    "定位高亮的动画没挂上 —— 亮一下的效果得有样式");

  const kf = /@keyframes library-locate-flash \{([\s\S]*?)\n\}/.exec(libCss);
  assert.ok(kf, "尺子自检：找不到 @keyframes library-locate-flash");
  assert.doesNotMatch(kf[1], /box-shadow/,
    "定位光晕不能画在 box-shadow 上：eagle-filter-bar.css 的 .library-card 带着 !important，"
    + "优先级高过 CSS 动画层，实测从没显示过");
  assert.match(kf[1], /outline:\s*3px solid var\(--accent\)/,
    "光晕改用 outline 画");
});

// ── 3. 减弱动效下定位高亮改成静态描边 ────────────────────────────
test("减弱动效：定位高亮不再靠动画，改成静态描边", () => {
  const blocks = reduceBlocks(libCss);
  assert.ok(blocks.length >= 1, "尺子自检：library.css 里一个 reduce 块都没有");
  const target = blocks.find((b) => b.includes("locate-flash"));
  assert.ok(target, "减弱动效下没人管定位高亮：动画被压成 0.01ms，等于没有反馈");
  assert.match(target, /animation:\s*none\s*!important/,
    "减弱动效下要显式关掉动画（全局那条只压 duration，不压 iteration-count）");
  assert.match(target, /outline:\s*3px solid var\(--accent\)/,
    "减弱动效下改成静态描边，亮到 class 被摘掉为止");
});

// ── 4. 每条无限动画都得有人管 ────────────────────────────────────
test("减弱动效：library.css 里每条 infinite 动画都被显式关掉", () => {
  const infLines = libCss.split("\n").filter((l) => /animation:[^;]*\binfinite\b/.test(l));
  assert.ok(infLines.length >= 1,
    "尺子自检：一条 infinite 都没扫到，尺子本身可能坏了");
  const blocks = reduceBlocks(libCss);
  for (const line of infLines) {
    const sel = line.split("{")[0].trim();
    const last = sel.split(/\s+/).pop();
    const covered = blocks.some((b) => b.includes(last) && /animation:\s*none/.test(b));
    assert.ok(covered,
      "这条无限动画在减弱动效下没人管（全局只压 duration，不压 iteration-count，会以 0.01ms 一直转）：" + sel);
  }
});

// ── 5. 减弱动效不该把指示本身抹掉 ────────────────────────────────
test("减弱动效：「处理中」的点还在，只是不再闪", () => {
  const orig = /\.answer-list-row\[data-state="working"\] \.answer-list-question::after \{([^}]*)\}/.exec(libCss);
  assert.ok(orig, "尺子自检：找不到 working 状态的原始规则");
  assert.match(orig[1], /background:\s*var\(--accent\)/,
    "处理中的那个点是靠底色认出来的，别把它抹掉");
  const blocks = reduceBlocks(libCss);
  const target = blocks.find((b) => b.includes('data-state="working"'));
  assert.ok(target, "减弱动效下没人管处理中的点");
  assert.match(target, /animation:\s*none\s*!important/, "减弱动效下别再无限闪");
});

// ── 6. 卡片过渡在减弱动效下要归零 ────────────────────────────────
test("减弱动效：卡片过渡归零（得压过那条 transition: all .15s ease !important）", () => {
  const base = /\.library-card \{([^}]*)\}/.exec(filterCss);
  assert.ok(base, "尺子自检：eagle-filter-bar.css 里找不到 .library-card 规则");
  assert.match(base[1], /transition:[^;]*!important/,
    "尺子自检：.library-card 上已经没有 !important 的 transition 了，这条断言要重写");

  const blocks = reduceBlocks(filterCss);
  const target = blocks.find((b) => /\.library-card\b/.test(b));
  assert.ok(target, "减弱动效下没人管卡片过渡：它带着 !important，比全局那条 transition-duration 赢");
  assert.match(target, /transition:\s*none\s*!important/,
    "同权重 + 靠后源序才压得过去");

  const baseAt = filterCss.indexOf(base[0]);
  const reduceAt = filterCss.indexOf(target);
  assert.ok(reduceAt > baseAt,
    "减弱动效的覆盖必须写在原来那条之后（同样带 !important 时靠源序决胜）");
});
