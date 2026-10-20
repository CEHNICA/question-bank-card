"use strict";
// 题库页「教材目录 + 试题篮」的落位（1.25.92）。
//
// 老师的原话：「这个试题栏啊会有 bug，当我开启那个目录的时候，试题栏会有 bug。」
//
// 真相：篮面板和题目列表被摆进了同一列，篮一展开就整片盖住题目，题目一道都点不着。
// 根因是两份样式在抢同一件事，谁赢跟「谁后加载」有关：
//   · library.css        第 296 行：篮面板放第 3 列
//   · library-reading.css 第 42 行：篮面板放第 2 列（这个文件在 HTML 里最后加载）
// 两条选择器一样具体（.library-page .library-workspace > .basket-panel），
// 后加载的 reading.css 赢 —— 平时不出事，因为没开目录时栅格本来就是两列。
// 一开目录，栅格变成「目录 | 题目 | 详情」三列，题目挪到第 2 列，
// 而篮子那条「第 2 列」的规则还活着：两个面板摞在同一列上。
//
// 这条测试不靠「规则长什么样」判断，而是照 CSS 的真实算法算出来：
// 解析两份样式表 → 算每条规则的选择器特异性和先后顺序 → 取真正生效的那一条
// → 断言篮面板和题目列表不在同一列。量的是结果，不是规则的文本。
const assert = require("node:assert/strict");
const fs = require("node:fs");

// ── 一、解析样式表 ──────────────────────────────────────────────
// 只取这两个文件，顺序就是 HTML 里的加载顺序（library.html 第 11 行、第 14 行）。
const STYLESHEETS = [
  { file: require.resolve("./library.css"), name: "library.css" },
  { file: require.resolve("./library-reading.css"), name: "library-reading.css" },
];

// 一条规则：选择器们 + 声明们 + 生效的媒体查询 + 全局先后顺序。
function parseStylesheet(text) {
  const rules = [];
  // 注释先去掉，否则「注释里也有一段 { }」会被当成规则。
  const src = text.replace(/\/\*[\s\S]*?\*\//g, "");

  // 递归：@media 里的规则并进来，并把媒体条件记在规则头上。
  function walk(body, media) {
    let i = 0;
    while (i < body.length) {
      while (i < body.length && /\s/.test(body[i])) i += 1;
      if (i >= body.length) return;
      if (body[i] === "@") {
        const brace = body.indexOf("{", i);
        if (brace === -1) return;
        const header = body.slice(i, brace).trim();
        let depth = 1;
        let j = brace + 1;
        while (j < body.length && depth > 0) {
          if (body[j] === "{") depth += 1;
          else if (body[j] === "}") depth -= 1;
          j += 1;
        }
        const inner = body.slice(brace + 1, j - 1);
        // 只认 @media：@keyframes/@supports 里的东西不是选择器，跳过。
        if (/^@media\b/i.test(header)) {
          walk(inner, media.concat([header.replace(/^@media\s*/i, "").trim()]));
        }
        i = j;
        continue;
      }
      const brace = body.indexOf("{", i);
      if (brace === -1) return;
      const selectorText = body.slice(i, brace).trim();
      let depth = 1;
      let j = brace + 1;
      while (j < body.length && depth > 0) {
        if (body[j] === "{") depth += 1;
        else if (body[j] === "}") depth -= 1;
        j += 1;
      }
      const declarations = body.slice(brace + 1, j - 1)
        .split(";")
        .map((piece) => piece.trim())
        .filter(Boolean)
        .map((piece) => {
          const colon = piece.indexOf(":");
          if (colon === -1) return null;
          const property = piece.slice(0, colon).trim().toLowerCase();
          let value = piece.slice(colon + 1).trim();
          const important = /!\s*important$/i.test(value);
          if (important) value = value.replace(/!\s*important$/i, "").trim();
          return { property, value, important };
        })
        .filter(Boolean);
      if (selectorText && declarations.length) {
        rules.push({
          selectors: splitSelectors(selectorText),
          declarations,
          media,
        });
      }
      i = j;
    }
  }

  walk(src, []);
  return rules;
}

function splitSelectors(text) {
  const parts = [];
  let buf = "";
  let depth = 0;
  for (const ch of text) {
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) {
      if (buf.trim()) parts.push(buf.trim());
      buf = "";
      continue;
    }
    buf += ch;
  }
  if (buf.trim()) parts.push(buf.trim());
  return parts;
}

const RULES = [];
for (const sheet of STYLESHEETS) {
  for (const rule of parseStylesheet(fs.readFileSync(sheet.file, "utf8"))) {
    rule.order = RULES.length;
    rule.source = sheet.name;
    RULES.push(rule);
  }
}

// ── 二、选择器怎么匹配 ──────────────────────────────────────────
// 拆成「复合选择器 + 关系符」，然后从右往左对真实层级链匹配。
function parseCompound(text) {
  const compound = { tag: null, id: null, classes: [], attrs: [], not: [] };
  let rest = text.trim();
  const tag = rest.match(/^[a-zA-Z][a-zA-Z0-9]*/);
  if (tag) {
    compound.tag = tag[0].toLowerCase();
    rest = rest.slice(tag[0].length);
  }
  while (rest.length) {
    let m;
    if ((m = rest.match(/^#([A-Za-z0-9_-]+)/))) {
      compound.id = m[1];
      rest = rest.slice(m[0].length);
    } else if ((m = rest.match(/^\.([A-Za-z0-9_-]+)/))) {
      compound.classes.push(m[1]);
      rest = rest.slice(m[0].length);
    } else if ((m = rest.match(/^\[([A-Za-z0-9_-]+)\]/))) {
      compound.attrs.push(m[1]);
      rest = rest.slice(m[0].length);
    } else if ((m = rest.match(/^:not\(([^)]*)\)/))) {
      for (const inner of m[1].matchAll(/\.([A-Za-z0-9_-]+)/g)) compound.not.push(inner[1]);
      rest = rest.slice(m[0].length);
    } else {
      rest = rest.slice(1);
    }
  }
  return compound;
}

function parseSelector(selector) {
  const tokens = [];
  let buf = "";
  let depth = 0;
  let pending = null;
  for (const ch of selector) {
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    if (depth === 0 && (ch === " " || ch === ">" || ch === "+" || ch === "~")) {
      if (buf.trim()) {
        tokens.push({ compound: parseCompound(buf), combinator: pending });
        buf = "";
        pending = null;
      }
      pending = ch === ">" ? ">" : " ";
      continue;
    }
    buf += ch;
  }
  if (buf.trim()) tokens.push({ compound: parseCompound(buf), combinator: pending });
  return tokens;
}

function matchCompound(compound, element) {
  if (!element) return false;
  if (compound.tag && compound.tag !== element.tag) return false;
  if (compound.id && compound.id !== element.id) return false;
  for (const c of compound.classes) if (!element.classes.has(c)) return false;
  for (const a of compound.attrs) if (!element.attrs.has(a)) return false;
  for (const n of compound.not) if (element.classes.has(n)) return false;
  return true;
}

function matchFrom(tokens, ti, chain, ci) {
  const token = tokens[ti];
  if (!matchCompound(token.compound, chain[ci])) return false;
  if (ti === 0) return true;
  if (token.combinator === ">") return matchFrom(tokens, ti - 1, chain, ci - 1);
  // 后代关系：往左找任意一个能接上的
  for (let k = ci - 1; k >= 0; k -= 1) {
    if (matchFrom(tokens, ti - 1, chain, k)) return true;
  }
  return false;
}

function matchesSelector(selector, chain) {
  const tokens = parseSelector(selector);
  if (!tokens.length) return false;
  return matchFrom(tokens, tokens.length - 1, chain, chain.length - 1);
}

// 特异性：(id 数, 类/属性/伪类数, 元素数)，按 CSS 的规矩比。
function specificity(selector) {
  let s = selector.replace(/:not\(([^)]*)\)/g, (_, inner) => inner);
  let ids = 0;
  let classes = 0;
  let types = 0;
  const take = (re, count) => {
    const hits = s.match(re) || [];
    s = s.replace(re, " ");
    return hits.length * count;
  };
  ids += take(/#[A-Za-z0-9_-]+/g, 1);
  classes += take(/\[[^\]]*\]/g, 1);
  classes += take(/\.[A-Za-z0-9_-]+/g, 1);
  types += take(/::[A-Za-z-]+/g, 1);
  classes += take(/:[A-Za-z-]+/g, 1);
  types += take(/\b[a-zA-Z][a-zA-Z0-9]*\b/g, 1);
  return [ids, classes, types];
}

function compareSpecificity(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

function mediaMatches(condition, width) {
  const cond = condition.trim();
  if (/^(only\s+)?screen\b/i.test(cond)) return true;
  if (/^(only\s+)?print\b/i.test(cond)) return false;
  return cond.split(/\s+and\s+/i).every((part) => {
    const m = part.match(/^\(\s*(min-width|max-width)\s*:\s*(\d+(?:\.\d+)?)px\s*\)$/i);
    if (!m) return false;
    return m[1].toLowerCase() === "min-width" ? width >= Number(m[2]) : width <= Number(m[2]);
  });
}

// ── 三、算出「这个元素最终被摆在第几列 / 什么定位」 ──────────────
// 层级链照着题库页真实结构拼：body > main.library-main > div.library-workspace > 目标。
function makeChain(target, bodyClasses, targetAttrs) {
  const el = (tag, classes, attrs = [], id = null) => ({
    tag,
    id,
    classes: new Set(classes),
    attrs: new Set(attrs),
  });
  return [
    el("html", []),
    el("body", ["library-page", ...bodyClasses]),
    el("main", ["library-main"]),
    el("div", ["library-workspace"]),
    el(target.tag, target.classes, targetAttrs, target.id || null),
  ];
}

function resolveProperty(chain, property, width) {
  let best = null;
  for (const rule of RULES) {
    if (!rule.media.every((condition) => mediaMatches(condition, width))) continue;
    for (const selector of rule.selectors) {
      if (!matchesSelector(selector, chain)) continue;
      const declaration = rule.declarations.find((d) => d.property === property);
      if (!declaration) continue;
      const candidate = {
        value: declaration.value,
        important: declaration.important,
        spec: specificity(selector),
        order: rule.order,
        selector,
        source: rule.source,
      };
      if (!best) {
        best = candidate;
        continue;
      }
      if (candidate.important !== best.important) {
        if (candidate.important) best = candidate;
        continue;
      }
      const diff = compareSpecificity(candidate.spec, best.spec);
      if (diff > 0 || (diff === 0 && candidate.order > best.order)) best = candidate;
    }
  }
  return best;
}

function scenario({ bodyClasses = [], width = 1442, basketOpen = true } = {}) {
  const basketChain = makeChain(
    { tag: "aside", classes: ["basket-panel"], id: "basketPanel" },
    bodyClasses,
    basketOpen ? [] : ["hidden"],
  );
  const resultsChain = makeChain(
    { tag: "section", classes: ["library-results"] },
    bodyClasses,
    [],
  );
  const pick = (chain, property) => {
    const found = resolveProperty(chain, property, width);
    return found ? found.value : null;
  };
  return {
    width,
    basket: {
      display: pick(basketChain, "display"),
      position: pick(basketChain, "position"),
      gridColumn: pick(basketChain, "grid-column"),
    },
    results: {
      display: pick(resultsChain, "display"),
      position: pick(resultsChain, "position"),
      gridColumn: pick(resultsChain, "grid-column"),
    },
  };
}

// 篮子在栅格里占着位置、题目也在栅格里占着位置 —— 两者的列号必须不一样。
// 篮子如果是 fixed（窄屏的浮层），它压根不在栅格里，不存在压住题目的事。
function assertNoOverlap(state, label) {
  const basketFloats = state.basket.position === "fixed";
  if (basketFloats) {
    assert.equal(state.basket.position, "fixed", `${label}：篮子应当是浮层`);
    return;
  }
  assert.equal(state.basket.display, "flex", `${label}：篮子面板是展开着的`);
  assert.notEqual(
    state.basket.gridColumn,
    state.results.gridColumn,
    `${label}：篮面板（${state.basket.gridColumn} 列）和题目列表（${state.results.gridColumn} 列）`
    + " 挤在同一列了 —— 篮一展开就把题目盖住了",
  );
}

// ── 四、四种真实场景 ────────────────────────────────────────────

// 1) 老师报的那个：开着目录再开篮。
{
  const state = scenario({ bodyClasses: ["library-directory-open", "library-basket-open"], width: 1442 });
  assert.equal(state.basket.gridColumn, "3", "目录开着时，篮面板要站在第三列（目录 | 题目 | 详情）");
  assert.equal(state.results.gridColumn, "2", "目录开着时，题目列表站在第二列");
  assertNoOverlap(state, "目录开着、篮开着");
}

// 2) 不开目录的常态：篮子还是老位置，题目还是第一列 —— 别把好的弄坏了。
{
  const state = scenario({ bodyClasses: ["library-basket-open"], width: 1442 });
  assert.equal(state.basket.gridColumn, "2", "不开目录时，篮面板照旧在第二列");
  assert.equal(state.results.gridColumn, "1", "不开目录时，题目列表照旧在第一列");
  assertNoOverlap(state, "目录关着、篮开着");
}

// 3) 开着目录又进了专注模式：篮子和题目照样得分开。
{
  const state = scenario({
    bodyClasses: ["library-directory-open", "library-basket-open", "library-focus-mode"],
    width: 1442,
  });
  assertNoOverlap(state, "目录开着、专注模式、篮开着");
}

// 4) 窄一点的窗口：目录变成盖在上面的浮层，篮子也是浮层，谁也不压谁。
{
  const state = scenario({ bodyClasses: ["library-directory-open", "library-basket-open"], width: 1000 });
  assertNoOverlap(state, "1000 像素宽、目录开着、篮开着");
  assert.equal(state.results.gridColumn, "1", "目录变浮层后，题目回到第一列");
}

// 5) 篮子收着的时候更不能出问题（收着的篮子还占着列就会多出一条空轨）。
{
  const state = scenario({ bodyClasses: ["library-directory-open"], width: 1442, basketOpen: false });
  assert.equal(state.basket.display, "none", "收着的篮子不占地方");
}

// ── 五、把根因钉住：两处「第 2 列」的来源不能悄悄再赢一次 ────────
// library-reading.css 是最后加载的，它那条「第 2 列」正是压住篮子的那一条。
// 目录那一段必须有一条更具体的规则把它顶掉，否则老师那边又会看见盖住题目的篮子。
{
  const reading = fs.readFileSync(require.resolve("./library-reading.css"), "utf8");
  assert.match(
    reading,
    /\.library-page \.library-workspace > \.question-inspector,\s*\n\s*\.library-page \.library-workspace > \.basket-panel \{ grid-column: 2; \}/,
    "reading.css 那条「篮和详情都放第二列」还在 —— 它是压住篮子的那一条，别顺手改掉，"
    + "免得普通的双列布局也乱了",
  );
  const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");
  assert.match(
    libraryCss,
    /body\.library-directory-open\.library-page \.library-workspace > \.basket-panel \{ grid-column: 3;/,
    "目录开着时，篮面板必须被明确挪到第三列",
  );
  assert.ok(
    !/body\.library-directory-open\.library-page \.library-workspace > \.basket-panel[^}]*grid-column: 2/.test(libraryCss),
    "目录开着时，篮面板不能被放回第二列 —— 那正是盖住题目的那一条",
  );
}

console.log("The basket and the question list never share a column: "
  + "with the textbook directory open the basket sits in the third column: OK");
