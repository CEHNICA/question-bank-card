// 第四十三轮：打印流里的表格不许冲出版心，卡片视图一个字都不许变。
//
// 这轮实测出的真缺陷（Chrome printToPDF 出的真 PDF）：
//   格子里 nowrap，宽表不许换行 → 13 列表右边缘 202.60mm（版心右缘只有 194mm），
//   20 列表 209.98mm（纸宽才 210mm，字印到纸边外），
//   连「三列 + 一格长文本」的表都冲到 196.60mm。
//   更糟的是：内容一旦比纸宽，Chrome 只能把整份卷子缩到 87% 印出来 ——
//   全卷的字都变小，页码那行也不再在纸面正中。
//
// 修法在打印作用域里：格子能换行；13 列以上的表再把最小列宽和内边距收一收。
// 卡片预览保持 nowrap（横向能拖着看），所以这条不许外溢到卡片上。
//
// 用 QB_RENDER_JS / QB_LIBRARY_CSS / QB_STYLES_CSS 喂旧版本可以自证看门狗有效。

const path = require("node:path");
const fs = require("node:fs");
const test = require("node:test");
const assert = require("node:assert/strict");

const ROOT = path.resolve(__dirname, "..");
function read(envName, rel) {
  return fs.readFileSync(process.env[envName] || path.join(ROOT, rel), "utf8");
}
const renderJs = read("QB_RENDER_JS", "frontend/qb-render.js");
const libraryCss = read("QB_LIBRARY_CSS", "frontend/library.css");
const stylesCss = read("QB_STYLES_CSS", "frontend/styles.css");

// 把 css 里所有「选择器里提到 .qb-table」的规则挑出来：{选择器, 声明}。
function tableRules(css) {
  const rules = [];
  const pattern = /[^{}]*\.qb-table[^{}]*\{([^{}]*)\}/g;
  let match;
  while ((match = pattern.exec(css)) !== null) {
    rules.push({ selector: match[0].slice(0, match[0].indexOf("{")).trim(), body: match[1] });
  }
  return rules;
}

function printScoped(selector) {
  return selector.includes(".print-flow") || selector.includes(".exam-page-body");
}

test("宽表会挂上 dense 类，列数够多才挂", () => {
  assert.match(renderJs, /const DENSE_TABLE_COLUMNS = (\d+);/);
  const threshold = Number(renderJs.match(/const DENSE_TABLE_COLUMNS = (\d+);/)[1]);
  assert.ok(threshold >= 13, `dense 的门槛太低了：${threshold} 列`);
  assert.match(
    renderJs,
    /width >= DENSE_TABLE_COLUMNS \? "qb-table qb-table-dense" : "qb-table"/,
    "列数够多时必须换成带 dense 的类名",
  );
  // 普通表不许挂 dense：门槛之外就是普通表。
  assert.doesNotMatch(renderJs, /qb-table-dense" : "qb-table qb-table-dense"/);
});

test("打印流里的格子能换行，两个作用域都要写", () => {
  const rules = tableRules(libraryCss);
  const wrapping = rules.filter(
    (rule) => printScoped(rule.selector) && /white-space:\s*normal/.test(rule.body),
  );
  assert.ok(wrapping.length > 0, "打印流里没有让格子换行的规则");
  const selectors = wrapping.map((rule) => rule.selector).join(" ");
  assert.match(selectors, /\.print-flow \.qb-table (td|th)/, "量高的那一份（print-flow）也得能换行");
  assert.match(selectors, /\.exam-page-body \.qb-table (td|th)/, "最终那几页（exam-page-body）也得能换行");
});

test("宽表连最小列宽和内边距一起收", () => {
  const rules = tableRules(libraryCss);
  const dense = rules.filter((rule) => rule.selector.includes(".qb-table-dense"));
  assert.ok(dense.length > 0, "没有针对 dense 表的规则");
  for (const rule of dense) {
    assert.ok(printScoped(rule.selector), `dense 规则跑出打印作用域了：${rule.selector}`);
    assert.match(rule.body, /min-width:\s*0/, "dense 表必须放开最小列宽，否则列一多又撑破版心");
    assert.match(rule.body, /padding:\s*\.15em \.45em/, "dense 表必须收紧内边距");
  }
});

// 只认「目标是单元格（td/th）」的规则。
// 容器级 `.qb-table { white-space: normal }` 是 styles.css 的既有写法，
// 会被单元格自己的 nowrap 覆盖掉，跟卡片视觉无关，不能拿它当缺陷。
function cellRules(css) {
  return tableRules(css).filter((rule) => /\.qb-table\b[^{}]*\b(td|th)\b/.test(rule.selector));
}

test("卡片视图一个字都不许变：表里的格子仍然不许换行", () => {
  const base = stylesCss.match(/\.qb-table td, \.qb-table th \{([^{}]*)\}/);
  assert.ok(base, "styles.css 里找不到 .qb-table td 的基础规则");
  assert.match(base[1], /white-space:\s*nowrap/, "卡片里的表格必须保持 nowrap（横向能拖着看）");
  // 凡是让单元格换行的规则，都必须锁在打印作用域里。
  for (const css of [libraryCss, stylesCss]) {
    for (const rule of cellRules(css)) {
      if (!/white-space:\s*normal/.test(rule.body)) continue;
      assert.ok(
        printScoped(rule.selector),
        `有规则把表格的换行改到了卡片上：${rule.selector}`,
      );
    }
  }
});
