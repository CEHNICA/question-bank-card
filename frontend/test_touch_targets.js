/* 第十八轮守护测试：窄屏（<=979px）触摸目标尺寸。
 *
 * 手机上控件太小 = 点不准。实测（375/414px）：
 *   - 200 个 .button-small 只有 28~30px 高
 *   - 41 个 <summary> 30px
 *   - 40 个 .library-card-select 复选框 17×17
 *   - 5 个 .eagle-capsule-btn 28px
 *
 * 分档（WCAG 2.2 / 平台指南）：
 *   普通可点控件 32×32；复选框/单选 24×24；卡片标签小徽章（小目标）24×24。
 *
 * 两个坑，写规则时别踩：
 *   1) 同 specificity 时源序在后者胜 —— 覆盖规则必须放在 library.css 末尾，
 *      否则被文件里更靠后的规则压回去。
 *   2) library.css 之后还加载了 4 个 CSS（library-tag-manager / tag-map /
 *      reading / eagle-filter-bar），它们里的同选择器会赢 —— 所以选择器必须
 *      带 body.library-page 前缀把特异性抬高一档。
 *
 * 宽屏是鼠标操作，不在这轮规矩里：所有规则必须关在 @media (max-width: 979px) 里，
 * 宽屏一行不许变（这条有单独断言）。
 *
 * 尺子自带自检：把修复抠掉，扫描器必须报出问题，否则断言恒真。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = __dirname;
// 看门狗/回归时可用 QB_LIB_CSS、QB_FILTER_CSS 指向临时副本（喂旧版本自证看门狗）。
const LIB_CSS = process.env.QB_LIB_CSS || path.join(FRONTEND, "library.css");
const FILTER_CSS = process.env.QB_FILTER_CSS || path.join(FRONTEND, "eagle-filter-bar.css");

/* ---------- 尺子：CSS 解析 ---------- */

// 去掉 CSS 注释（注释里的花括号会破坏配平）。
// 用等长空格替换而不是直接删掉 —— 直接删会让后面所有字符的坐标前移，
// 末尾判定就会切错位置（实测 library.css 末尾判定因此失真）。
function stripCssComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => " ".repeat(m.length));
}

// 简单可靠版：把 CSS 摊平成「顶层块」序列。
//
// 注意：CSS 里 content: "}" 这种字符串含有花括号，直接配平会把后面全部吞掉
// （实测 library.css 末尾 16KB 被一个 media 块吃掉）。所以配平时必须跳过引号字符串。
function parseTopLevel(css) {
  const src = stripCssComments(css);
  const out = [];
  let i = 0;
  while (i < src.length) {
    const brace = src.indexOf("{", i);
    if (brace < 0) break;
    const head = src.slice(i, brace).replace(/\s+/g, " ").trim();
    // 从 brace 开始配平，遇到引号字符串就整段跳过
    let depth = 0;
    let j = brace;
    while (j < src.length) {
      const ch = src[j];
      if (ch === '"' || ch === "'") {
        const quote = ch;
        j++;
        while (j < src.length && src[j] !== quote) {
          if (src[j] === "\\") j++;
          j++;
        }
        j++; // 跳过收尾引号
        continue;
      }
      if (ch === "{") depth++;
      else if (ch === "}") {
        depth--;
        if (depth === 0) break;
      }
      j++;
    }
    out.push({ head, body: src.slice(brace + 1, j), start: i, end: j + 1 });
    i = j + 1;
  }
  return out;
}

// 收集所有规则：@media (max-width: N) 里的内层规则会带上 media 标记
function collectRules(css) {
  const rules = [];
  for (const block of parseTopLevel(css)) {
    if (block.head.startsWith("@media")) {
      const media = block.head.replace(/^@media\s*/, "").trim();
      for (const inner of parseTopLevel(block.body)) {
        rules.push({
          media,
          selector: inner.head,
          decls: inner.body.replace(/\s+/g, " ").trim(),
          start: block.start,
          end: block.end,
          top: true,
        });
      }
    } else if (!block.head.startsWith("@")) {
      rules.push({
        media: null,
        selector: block.head,
        decls: block.body.replace(/\s+/g, " ").trim(),
        start: block.start,
        end: block.end,
        top: true,
      });
    }
  }
  return rules;
}

function isNarrow(media) {
  return !!media && /max-width/i.test(media);
}

// 选择器是否命中需求里的 class 组合（按 token 子串匹配）
function selectorMatches(selector, tokens) {
  return tokens.every((t) => selector.includes(t));
}

// 声明里是否含 prop: value
function hasDecl(decls, prop, value) {
  const re = new RegExp(
    "(^|;)\\s*" + prop.replace(/[-]/g, "\\-") + "\\s*:\\s*" + value.replace(/[-]/g, "\\-") + "\\s*(;|$)"
  );
  return re.test(decls);
}

/* ---------- 需求表 ---------- */

// 每条需求：选择器 token + 必须同时存在的声明
const LIB_SPECS = [
  {
    name: "小号按钮（卡片操作区）",
    tokens: [".button", ".small"],
    decls: [["min-height", "32px"]],
    scope: "narrow",
  },
  {
    name: "卡片操作区按钮",
    tokens: [".library-card-actions", ".button"],
    decls: [["min-height", "32px"]],
    scope: "narrow",
  },
  {
    name: "卡片折叠 summary",
    tokens: [".library-card-more", "summary"],
    decls: [["min-height", "32px"]],
    scope: "narrow",
  },
  {
    name: "题目信息 summary",
    tokens: [".question-inspector-info", "summary"],
    decls: [["min-height", "32px"]],
    scope: "narrow",
  },
  {
    name: "标签编辑按钮",
    tokens: [".library-tags-edit"],
    decls: [["min-height", "32px"]],
    scope: "narrow",
  },
  {
    name: "卡片复选框",
    tokens: [".library-card-select"],
    decls: [["width", "24px"], ["height", "24px"]],
    scope: "narrow",
  },
  {
    name: "批量选择复选框",
    tokens: [".library-bulk", "input[type"],
    decls: [["width", "24px"], ["height", "24px"]],
    scope: "narrow",
  },
  {
    name: "卡片标签小徽章（小目标档）",
    tokens: [".library-card-tag-glimpse"],
    decls: [["min-height", "24px"]],
    scope: "narrow",
  },
];

const FILTER_SPECS = [
  {
    name: "筛选胶囊按钮",
    tokens: [".eagle-capsule-btn"],
    decls: [["height", "32px"]],
    scope: "narrow",
  },
];

// 扫描一份 CSS，返回未满足的需求名列表
function scanSpecs(css, specs) {
  const rules = collectRules(css);
  const missing = [];
  for (const spec of specs) {
    const hit = rules.some((r) => {
      if (spec.scope === "narrow" && !isNarrow(r.media)) return false;
      if (!selectorMatches(r.selector, spec.tokens)) return false;
      return spec.decls.every(([p, v]) => hasDecl(r.decls, p, v));
    });
    if (!hit) missing.push(spec.name);
  }
  return missing;
}

// 规则是否必须带 body 前缀（抬特异性，防后加载 CSS 压制）
function specsWithoutBodyPrefix(css, specs) {
  const rules = collectRules(css);
  const bad = [];
  for (const spec of specs) {
    const hits = rules.filter(
      (r) => isNarrow(r.media) && selectorMatches(r.selector, spec.tokens)
    );
    if (!hits.length) continue;
    const ok = hits.some((r) => /body\.library-page/.test(r.selector));
    if (!ok) bad.push(spec.name);
  }
  return bad;
}

// 宽屏污染检查：这些声明不许出现在窄屏媒体查询之外
function wideScreenLeaks(css, specs) {
  const rules = collectRules(css);
  const leaks = [];
  for (const spec of specs) {
    for (const r of rules) {
      if (r.media === null && selectorMatches(r.selector, spec.tokens)) {
        const all = spec.decls.every(([p, v]) => hasDecl(r.decls, p, v));
        if (all) leaks.push(`${spec.name} → ${r.selector}`);
      }
    }
  }
  return leaks;
}

// 末尾检查：窄屏块必须位于文件末尾（后面不能再有别的规则）
function narrowBlockNotAtEnd(css) {
  const blocks = parseTopLevel(css).filter(
    (b) => b.head.startsWith("@media") && /max-width/i.test(b.head)
  );
  if (!blocks.length) return true;
  const last = blocks[blocks.length - 1];
  const tail = stripCssComments(css.slice(last.end)).replace(/\s+/g, "");
  // true = 窄屏块后面还有别的规则（不在末尾，会被同文件后续规则按源序压制）
  return tail !== "";
}

/* ---------- 尺子自检用的合成源码 ---------- */

const SAMPLE_OK = `
.library-page .button.small { min-height: 28px; }
@media (max-width: 979px) {
  body.library-page .button.small,
  body.library-page .button.button-small,
  body.library-page .library-card-actions .button {
    min-height: 32px;
  }
  body.library-page .library-card-more > summary,
  body.library-page .question-inspector-info > summary,
  body.library-page .library-tags-edit { min-height: 32px; }
  body.library-page .library-card-select,
  body.library-page .library-bulk input[type="checkbox"] { width: 24px; height: 24px; }
  body.library-page .library-card-tag-glimpse { min-height: 24px; }
}
`;

// 抠掉 32px 那条（模拟修复被撤）
const SAMPLE_BROKEN = SAMPLE_OK.replace(/min-height: 32px;/g, "min-height: 28px;");

// 抠掉 body 前缀（模拟特异性被降级）
const SAMPLE_NO_PREFIX = SAMPLE_OK.replace(/body\.library-page /g, "");

// 窄屏块前面塞了一条后加载规则（模拟源序被压）
const SAMPLE_NOT_LAST = `
@media (max-width: 979px) {
  body.library-page .button.small { min-height: 32px; }
}
.library-page .button.small { min-height: 28px; }
`;

/* ---------- 测试 ---------- */

const libCss = fs.readFileSync(LIB_CSS, "utf8");
const filterCss = fs.readFileSync(FILTER_CSS, "utf8");

test("尺子自检：合成源码能被正确扫描（缺什么报什么）", () => {
  assert.deepStrictEqual(scanSpecs(SAMPLE_OK, LIB_SPECS), [], "合格样本不该报缺失");
  const broken = scanSpecs(SAMPLE_BROKEN, LIB_SPECS);
  assert.ok(broken.length >= 1, "把 32px 抠掉后必须报缺失");
  assert.ok(broken.includes("小号按钮（卡片操作区）"), "必须精确报出被抠掉的那条");
});

test("尺子自检：body 前缀检测与末尾检测有效", () => {
  assert.deepStrictEqual(specsWithoutBodyPrefix(SAMPLE_OK, LIB_SPECS), []);
  assert.ok(
    specsWithoutBodyPrefix(SAMPLE_NO_PREFIX, LIB_SPECS).length >= 1,
    "去掉 body 前缀必须被报出来"
  );
  assert.strictEqual(narrowBlockNotAtEnd(SAMPLE_OK), false, "合格样本的窄屏块在末尾");
  assert.strictEqual(narrowBlockNotAtEnd(SAMPLE_NOT_LAST), true, "块不在末尾必须报出来");
});

test("D1 窄屏触摸目标规则齐全（library.css）", () => {
  const missing = scanSpecs(libCss, LIB_SPECS);
  assert.deepStrictEqual(missing, [], "library.css 缺这些窄屏触摸目标规则：" + missing.join("、"));
});

test("D2 窄屏触摸目标规则带 body.library-page 前缀（压过后加载 CSS）", () => {
  const bad = specsWithoutBodyPrefix(libCss, LIB_SPECS);
  assert.deepStrictEqual(bad, [], "这些规则必须抬特异性：" + bad.join("、"));
});

test("D3 窄屏块在 library.css 末尾（源序不被压制）", () => {
  assert.strictEqual(narrowBlockNotAtEnd(libCss), false, "窄屏触摸目标块必须放在文件末尾");
});

test("D4 筛选胶囊按钮在窄屏达 32px（eagle-filter-bar.css）", () => {
  const missing = scanSpecs(filterCss, FILTER_SPECS);
  assert.deepStrictEqual(missing, [], "eagle-filter-bar.css 缺：" + missing.join("、"));
  assert.strictEqual(
    narrowBlockNotAtEnd(filterCss),
    false,
    "eagle-filter-bar.css 的窄屏块必须在文件末尾"
  );
});

test("D5 宽屏零变化：触摸目标声明不许跑出窄屏媒体查询", () => {
  const leaks = wideScreenLeaks(libCss, LIB_SPECS).concat(
    wideScreenLeaks(filterCss, FILTER_SPECS)
  );
  assert.deepStrictEqual(leaks, [], "这些声明漏到了宽屏：" + leaks.join("、"));
});
