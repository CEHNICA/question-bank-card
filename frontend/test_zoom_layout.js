// 第二十轮守护测试：大字号 / 窄屏下的布局健壮性。
//
// 背景：用户把浏览器「最小字体大小」调大（Chrome 设置 → 外观 → 字体），
// 文字会撑大而盒子不变。项目里几乎全用 px（rem 用量为 0），盒子不会跟着长，
// 于是出现四种坏相：
//   ① 顶栏的「切换到 Eagle 现代视图」被 flex 压成一列竖排字（320px 下 63×98）；
//   ② 胶囊组/待办条不许收缩，最小字体下把整页撑出横向滚动条；
//   ③ 「智能文件夹」被挤扁，文字溢出按钮（上下各 4px）或塌成一列竖字；
//   ④ 目录栏的分类徽标不换行，24px 字下越出左栏、压到中间栏的题卡上。
//
// 这里的断言是**静态源码断言**：CI 里没有浏览器，跑不了真布局。
// 所以尺子必须自己会认规则 —— 第一条就是尺子自检（拿一条一定存在的规则去探，
// 探不到就红），免得看门狗看着在跑、其实断言恒真。
//
// 想自证看门狗有效，可以用环境变量喂旧版副本：
//   QB_LIB_CSS=旧版library.css QB_FILTER_CSS=旧版eagle-filter-bar.css node --test frontend/test_zoom_layout.js

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const LIB_CSS = process.env.QB_LIB_CSS || path.join(ROOT, "frontend", "library.css");
const FILTER_CSS = process.env.QB_FILTER_CSS || path.join(ROOT, "frontend", "eagle-filter-bar.css");

// ── 迷你 CSS 解析器 ────────────────────────────────────────────────
// 注释用等长空格遮罩（直接删会让后面所有字符坐标前移，末尾判定会切错位置）。
function maskComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => " ".repeat(m.length));
}

// 规则表：{ media, selectors: [...], body }
// 关键点：@media 的内容要按花括号配平取出来再递归，
// 用「([^{}]+)\{([^{}]*)\}」去匹会只吃到空 body，媒体查询里的规则全丢。
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

// 声明表：{ prop: value }
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

// 合并命中规则的声明（后写的覆盖先写的，等同于层叠结果）
function merged(rules, matcher) {
  const out = {};
  for (const r of rulesFor(rules, matcher)) Object.assign(out, decls(r.body));
  return out;
}

// 某条媒体查询块里命中选择器的规则
function inMedia(rules, mediaNeedle, matcher) {
  return rules.filter(
    (r) => r.media && r.media.includes(mediaNeedle) && r.selectors.some((s) => matcher(s)),
  );
}

// 只在媒体查询之外（全局）的规则
function globalRules(rules, selector) {
  return rules.filter((r) => !r.media && r.selectors.includes(selector));
}

const libraryCss = fs.readFileSync(LIB_CSS, "utf8");
const filterCss = fs.readFileSync(FILTER_CSS, "utf8");
const libRules = collectRules(libraryCss);
const filterRules = collectRules(filterCss);

test("尺子自检：解析器认得已知规则，且不是恒真", () => {
  // .library-search 在 library.css 里一定存在
  const libHit = rulesFor(libRules, (s) => s === ".library-search");
  assert.ok(libHit.length > 0, "解析器没认出 .library-search，尺子坏了");
  // .eagle-capsule-btn 在 eagle-filter-bar.css 里一定存在
  const filterHit = rulesFor(filterRules, (s) => s === ".eagle-capsule-btn");
  assert.ok(filterHit.length > 0, "解析器没认出 .eagle-capsule-btn，尺子坏了");
  // 合并出来的声明必须真的有值（防「匹配到了但取不到属性」的恒真）
  const libDecl = merged(libRules, (s) => s === ".library-search");
  assert.ok(libDecl.position !== undefined, "合并声明取不到属性，尺子坏了");
  // 媒体查询里的规则也要扫得到（扫不到 → 后面的断言会假红或恒真）
  const mediaRules = libRules.filter((r) => r.media);
  assert.ok(mediaRules.length > 0, "解析器没认出 @media 里的规则，尺子坏了");
  // 合并器在多条同名规则上要取最后一条（层叠顺序），不能取第一条
  const cascade = merged(
    [{ selectors: [".x"], body: "color: red;" }, { selectors: [".x"], body: "color: blue;" }],
    (s) => s === ".x",
  );
  assert.strictEqual(cascade.color, "blue", "合并器没有按层叠顺序取最后一条，尺子坏了");
});

test("D1 窄屏顶栏换行：搜索独占一行，Eagle 链接不再被压成竖排字", () => {
  const blocks = inMedia(libRules, "max-width: 560px", (s) =>
    s.includes(".library-top-navigation"),
  );
  assert.ok(blocks.length > 0, "library.css 里找不到 ≤560px 的 .library-top-navigation 规则");

  const nav = merged(blocks, (s) => s.includes(".library-top-navigation"));
  assert.strictEqual(nav["flex-wrap"], "wrap", "窄屏顶栏没有 flex-wrap: wrap");

  const search = merged(blocks, (s) => s.includes(".library-search"));
  const flex = search.flex || "";
  assert.ok(
    /100%/.test(flex),
    `窄屏搜索框没有独占一行（flex 应含 100%），实际：${flex || "（未声明）"}`,
  );

  // 链接本身得允许收缩，不能被压成竖排
  const link = merged(blocks, (s) => s.includes(".library-top-navigation > a"));
  assert.ok(
    link["min-width"] === "0" || (link.flex || "").includes("1"),
    "窄屏的 Eagle 链接没有可收缩约束",
  );
});

test("D2 待办条可收缩：nowrap 不再把整页撑宽", () => {
  const todos = merged(libRules, (s) => s.includes(".library-todos:not([hidden])"));
  assert.ok(todos.flex !== undefined, "找不到 .library-todos:not([hidden]) 的规则");
  assert.ok(
    !/0 0 auto/.test(todos.flex),
    `待办条还是 flex: 0 0 auto（不许缩），实际：${todos.flex}`,
  );
  assert.strictEqual(todos["min-width"], "0", "待办条缺 min-width: 0");

  const bits = merged(libRules, (s) => s.includes(".library-todo-bits"));
  assert.strictEqual(bits.overflow, "hidden", "待办条后半句没有 overflow: hidden");
  assert.strictEqual(bits["text-overflow"], "ellipsis", "待办条后半句没有省略号截断");
});

test("D3 胶囊组允许收缩，但只有窄屏才换行（宽屏必须一行到底）", () => {
  const group = merged(filterRules, (s) => s === ".eagle-capsules-group");
  assert.ok(group["flex-shrink"] !== undefined, "找不到 .eagle-capsules-group 的规则");
  assert.strictEqual(group["flex-shrink"], "1", "胶囊组不许收缩，放大后会把整页撑宽");
  assert.strictEqual(group["min-width"], "0", "胶囊组缺 min-width: 0");

  // 宽屏（全局）不许换行：一旦全局换行，1280px 原始态下胶囊组会从 543px
  // 被挤到 525px 再折成两行，筛选条整条长高 32px，下方内容全体下移。
  const globalGroup = globalRules(filterRules, ".eagle-capsules-group");
  assert.ok(globalGroup.length > 0, "找不到 .eagle-capsules-group 的全局规则");
  for (const rule of globalGroup) {
    const d = decls(rule.body);
    assert.strictEqual(
      d["flex-wrap"],
      undefined,
      "胶囊组在全局就换行了 —— 宽屏会因换行多出一行，页面整体下移",
    );
  }

  // 窄屏才允许换行
  const narrow = inMedia(filterRules, "max-width: 979px", (s) => s === ".eagle-capsules-group");
  assert.ok(narrow.length > 0, "找不到 ≤979px 的 .eagle-capsules-group 规则");
  const narrowDecl = merged(narrow, (s) => s === ".eagle-capsules-group");
  assert.strictEqual(narrowDecl["flex-wrap"], "wrap", "窄屏下胶囊组不换行，会被挤成竖排字");
});

test("D4 智能文件夹：文字单行 + 省略号，按钮不许被挤扁", () => {
  const label = merged(filterRules, (s) => s.includes(".eagle-smart-folder-btn > span:last-child"));
  assert.strictEqual(label["white-space"], "nowrap", "智能文件夹文字没有 nowrap（会塌成一列竖字）");
  assert.strictEqual(label["text-overflow"], "ellipsis", "智能文件夹文字没有省略号截断");
  assert.strictEqual(label.overflow, "hidden", "智能文件夹文字没有 overflow: hidden");

  // 按钮本身：可以缩，但不许超出容器
  const btn = merged(filterRules, (s) => s === ".eagle-smart-folder-btn");
  assert.strictEqual(btn["min-width"], "0", "智能文件夹按钮缺 min-width: 0");
  assert.strictEqual(btn["max-width"], "100%", "智能文件夹按钮不许超出容器");

  // 外层胶囊在宽屏不许收缩：收缩会把按钮挤到 83px，
  // 「智能文件夹」五个字被截成「智能文…」（实测 scrollWidth 60 > clientWidth 38）。
  const capsule = merged(filterRules, (s) => s === ".eagle-smart-folder-capsule");
  assert.strictEqual(capsule["flex-shrink"], "0", "智能文件夹胶囊在宽屏不许收缩（会被挤到半个词）");
  assert.strictEqual(capsule["min-width"], "0", "智能文件夹胶囊缺 min-width: 0");
});

test("D5 不许用 -webkit-box/-webkit-line-clamp 钳行数", () => {
  // 踩过的坑：-webkit-box 的 min-content 是整行文字的宽度，
  // 会让按钮不肯缩到 83px 以下 → 胶囊组被挤窄换行 → 整个筛选条长高 32px。
  // 想钳行数就钳行数，但别用会改掉内在宽度的东西。
  for (const rule of filterRules) {
    const d = decls(rule.body);
    assert.strictEqual(
      d["-webkit-line-clamp"],
      undefined,
      `${rule.selectors.join(",")} 用了 -webkit-line-clamp（会改内在宽度，宽屏会位移）`,
    );
  }
  const smartLabel = merged(filterRules, (s) => s.includes(".eagle-smart-folder-btn > span:last-child"));
  assert.notStrictEqual(smartLabel.display, "-webkit-box", "智能文件夹文字不许用 display: -webkit-box");
});

test("D6 目录栏的分类/学段切换不许越栏", () => {
  const kinds = merged(libRules, (s) => s.includes(".library-directory-kinds"));
  assert.strictEqual(kinds["flex-wrap"], "wrap", "目录分类切换不换行，字号放大后会越出左栏");

  const chip = merged(libRules, (s) => s.includes(".library-directory-kind"));
  assert.strictEqual(chip["min-width"], "0", "目录分类徽标缺 min-width: 0（不许硬撑）");
});

test("D7 收缩类改动不许顺手改尺寸", () => {
  // 这一轮只允许「能不能缩 / 换不换行」的变化，高度/内边距/字号一律不许动，
  // 否则宽屏就会出现肉眼可见的位移。
  const forbidden = ["height", "min-height", "max-height", "padding", "font-size"];
  // 本轮新加的选择器：这些规则里出现尺寸属性就是越界
  // 只查本轮**新建**的选择器；既有的 .eagle-capsule-label 本来就有 padding，
  // 拿它当判据会把旧版也判红，等于尺子指错了地方。
  const fresh = [
    ".eagle-capsule-wrapper",
    ".eagle-smart-folder-btn > span:last-child",
  ];
  for (const rule of filterRules) {
    if (!rule.selectors.some((s) => fresh.includes(s))) continue;
    const d = decls(rule.body);
    for (const prop of forbidden) {
      assert.ok(
        d[prop] === undefined,
        `${rule.selectors.join(",")} 改了 ${prop}（${d[prop]}）—— 这一轮只准改能不能缩`,
      );
    }
  }
  // 这两个容器的全局规则里也不许出现尺寸属性
  for (const sel of [".eagle-capsules-group", ".eagle-smart-folder-capsule"]) {
    const global = globalRules(filterRules, sel);
    assert.ok(global.length > 0, `找不到 ${sel} 的全局规则`);
    for (const rule of global) {
      const d = decls(rule.body);
      for (const prop of forbidden) {
        assert.ok(
          d[prop] === undefined,
          `${sel} 的全局规则改了 ${prop}（${d[prop]}）—— 这一轮只准改能不能缩`,
        );
      }
    }
  }
});
