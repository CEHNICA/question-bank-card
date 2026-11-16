// 第二十四轮：Windows 高对比度模式（forced-colors: active）。
//
// 这轮体检在题库页实测出三条缺陷（Playwright forced_colors="active" 对照 normal）：
//   1. 勾选题卡后，该卡 class 是 "library-card inspector-active compact is-selected"，
//      但 forced 下选中卡与未选中卡的 computed 样式零差异 —— border-color 被系统
//      接管、box-shadow 被移除，用户根本看不出自己勾了哪一张。
//   2. 提示条和抽屉都变成「白底 + border 0px + shadow none」，浮在页面上完全没有边界。
//   3. 禁用按钮的系统 DisabledText 色（rgb(96,0,0)）再叠一层 opacity: .45，
//      实际对比度只剩 2.93:1，远低于 4.5:1。
//
// 修法都在 frontend/library.css 的 @media (forced-colors: active) 块里：
// 系统接管的是 color / background-color / border-color / box-shadow，
// 凡是靠这些属性单独传达的状态，都得换成系统色管不到的 outline / 字重 / 边框。
//
// 守护测试是静态源码断言；实测那部分由 _audit_fc*.py 负责。
// 用 QB_LIB_CSS 喂旧版本可以自证看门狗有效。

const path = require("node:path");
const fs = require("node:fs");
const test = require("node:test");
const assert = require("node:assert/strict");

const ROOT = path.resolve(__dirname, "..");
function read(envName, rel) {
  return fs.readFileSync(process.env[envName] || path.join(ROOT, rel), "utf8");
}
const rawCss = read("QB_LIB_CSS", "frontend/library.css");

// 先剥掉注释 —— 否则注释里写的 "forced-colors: active" 字样会被当成真规则。
const css = rawCss.replace(/\/\*[\s\S]*?\*\//g, "");

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

// 所有 forced-colors 媒体块（去注释之后）。
function forcedBlocks(src) {
  const out = [];
  const re = /@media\s*\(forced-colors\s*:\s*active\)\s*\{/g;
  let m;
  while ((m = re.exec(src))) {
    out.push(blockAt(src, m.index + m[0].length - 1));
  }
  return out;
}

// 在块里找一条规则：选择器（可含逗号分隔）→ 声明体。
function ruleIn(block, selector) {
  // 选择器段和声明段都不含花括号 —— 块里没有嵌套，够用。
  // （别用 (?:^|[{};]) 打头：那种写法会把上一条规则的结尾 } 吃掉，
  //  后面所有规则都配不上了，只能命中第一条。）
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(block))) {
    const sels = m[1].split(",").map((s) => s.trim());
    if (sels.includes(selector)) return m[2];
  }
  return null;
}

// 选择器特异性：(class/attr/伪类, id, 元素) → 比较用整数。
// 简化到够用：类选择器 + 属性选择器 + 伪类各算 10，id 算 100，元素/伪元素算 1。
function specificity(selector) {
  const ids = (selector.match(/#[\w-]+/g) || []).length;
  const classes = (selector.match(/\.[\w-]+/g) || []).length
    + (selector.match(/\[[^\]]+\]/g) || []).length
    + (selector.replace(/:not\([^)]*\)/g, "").match(/:[\w-]+/g) || []).length;
  const elements = (selector.replace(/::?[\w-]+(\([^)]*\))?/g, "")
    .replace(/[.#][\w-]+/g, "")
    .replace(/\[[^\]]+\]/g, "")
    .match(/\b[a-zA-Z][\w-]*\b/g) || []).length;
  return classes * 100 + ids * 10000 + elements;
}

// ── 0. 尺子自检：解析器坏了的话下面全是恒真 ────────────────────────
test("尺子自检：forced-colors 块和规则查找器都能用", () => {
  const blocks = forcedBlocks(css);
  assert.ok(blocks.length >= 1,
    "尺子自检：library.css 里一个 @media (forced-colors: active) 块都没有");

  // 双向自检：用块里真实存在的第一条规则试查找器（不写死选择器，
  // 否则看门狗逐项退回时自检会跟着红，就分不清是谁坏了）。
  const first = /([^{}]+)\{([^{}]*)\}/.exec(blocks[0]);
  assert.ok(first, "尺子自检：forced-colors 块里一条规则都解析不出来");
  const firstSel = first[1].split(",")[0].trim();
  assert.ok(ruleIn(blocks[0], firstSel),
    "尺子自检：规则查找器连块里真实存在的规则都找不到：" + firstSel);
  assert.equal(ruleIn(blocks[0], "body.library-page .library-card.没有这条规则"), null,
    "尺子自检：规则查找器对不存在的选择器也返回了东西，这把尺子会把什么都算成有");
  assert.ok(specificity("body.library-page .library-card.is-selected")
    > specificity(".library-page .library-card.inspector-active"),
    "尺子自检：特异性算错了");
});

// ── 1. 勾选状态必须在高对比度下能看出来 ────────────────────────────
test("高对比度：勾选题卡要靠 outline 画出来", () => {
  const blocks = forcedBlocks(css);
  const sel = "body.library-page .library-card.is-selected";
  const decl = ruleIn(blocks[0], sel);
  assert.ok(decl, "高对比度下没人管勾选题卡：边框色被系统接管、阴影被移除，"
    + "选中卡和未选中卡看起来一模一样");

  assert.match(decl, /outline:\s*3px solid\s+Highlight/,
    "勾选态改用系统高亮色画 outline（别用 border-color，那个会被系统接管）");
  assert.match(decl, /outline-offset:\s*-3px/,
    "outline 要向内画，免得被列表容器裁掉");

  // 特异性必须压过 library-reading.css 的 .library-page .library-card.inspector-active
  // （那条是 0-3-0 且写了 outline: 0，之前就是被它压掉的）。
  assert.ok(specificity(sel) > specificity(".library-page .library-card.inspector-active"),
    "这条规则的特异性必须高于 library-reading.css 里的 .library-page .library-card.inspector-active"
    + "（那条写着 outline: 0，压不过它就等于没写）");
});

test("高对比度：勾选题卡的焦点环不能被顶掉", () => {
  const blocks = forcedBlocks(css);
  const sel = "body.library-page .library-card.is-selected:focus-visible";
  const decl = ruleIn(blocks[0], sel);
  assert.ok(decl, "高对比度下勾选态聚焦没有单独的规则：向内的 outline 会把焦点环吃掉");
  assert.match(decl, /outline-offset:\s*3px/,
    "聚焦时 outline 要挪到外侧，别跟焦点环叠在一起");
});

// ── 2. 禁用按钮别再叠透明度 ────────────────────────────────────────
test("高对比度：禁用按钮不再叠 opacity", () => {
  const blocks = forcedBlocks(css);
  const decl = ruleIn(blocks[0], "button:disabled");
  assert.ok(decl, "高对比度下没人管禁用按钮：系统 DisabledText 色再叠 opacity: .45，"
    + "实测对比度只剩 2.93:1");
  assert.match(decl, /opacity:\s*1\b/,
    "禁用态别再叠透明度，把系统的 DisabledText 完整让出来");
});

// ── 3. 浮层要有边界 ────────────────────────────────────────────────
test("高对比度：提示条和抽屉要靠边框撑出边界", () => {
  const blocks = forcedBlocks(css);
  for (const sel of ["body .toast[popover]", "body .site-drawer"]) {
    const decl = ruleIn(blocks[0], sel);
    assert.ok(decl, "高对比度下没人管 " + sel + "：阴影被移除、边框被清零，"
      + "浮层在页面上完全没有边界");
    assert.match(decl, /border:\s*1px solid\s+CanvasText/,
      sel + " 要用系统正文色画一条边框");
  }
});

// ── 4. 位置约束：这块不能放在窄屏块之后 ────────────────────────────
test("高对比度：覆盖块要放在窄屏 media 块之前", () => {
  const forcedAt = css.indexOf("@media (forced-colors: active)");
  const narrowAt = css.lastIndexOf("@media (max-width: 979px)");
  assert.ok(forcedAt >= 0, "尺子自检：找不到 forced-colors 块");
  assert.ok(narrowAt >= 0, "尺子自检：找不到窄屏 media 块");
  assert.ok(forcedAt < narrowAt,
    "forced-colors 块必须排在窄屏 media 块之前 —— 窄屏块要在文件末尾"
    + "（见 test_touch_targets.js），不能被它挤到后面去");
});
