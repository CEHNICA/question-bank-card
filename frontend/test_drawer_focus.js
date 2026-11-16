/* 第十七轮守护测试：窄屏下右侧详情抽屉的焦点管理。
 *
 * 抽屉在窄屏（<=979px）是模态：有遮罩、盖住整屏。可是焦点完全没管：
 *   D1 关闭态：抽屉只是被 translateX(100%) 挪到屏幕外，元素还在，
 *              里面的「收起详情」照样能被键盘聚焦 —— 焦点掉进屏幕外，什么都看不见。
 *   D2 打开态：焦点不进抽屉，还停在触发按钮上。
 *   D3 打开态：抽屉里没有焦点循环，Tab 直接跑到背后的题目列表上。
 *   D4 关闭态：点「收起详情」关掉后，焦点停在已经移出屏外的按钮上，不还给触发按钮。
 *
 * 修法用 inert（同步生效、不挡过渡）。别改回 CSS visibility: hidden ——
 * 打开抽屉时 visibility 还停在过渡起点，focus() 会被浏览器拒掉，焦点进不去。
 *
 * 宽屏下抽屉是常驻第三栏，不是模态 —— 这套规矩只在窄屏生效，宽屏一行不许变。
 *
 * 尺子自带自检：把修复抠掉，扫描器必须报出问题，否则断言恒真。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const FRONTEND = __dirname;
const LIB_JS = path.join(FRONTEND, "library.js");
const LIB_CSS = path.join(FRONTEND, "library.css");

/* ---------- 尺子 ---------- */

function extractFunction(src, signature) {
  const start = src.indexOf(signature);
  if (start < 0) return null;
  const open = src.indexOf("{", start);
  if (open < 0) return null;
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  return null;
}

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^[ \t]*\/\/.*$/gm, " ");
}

// 取出所有 @media (max-width: 979px) 块的内容（窄屏专属规则都在这儿）
function narrowBlocks(css) {
  const blocks = [];
  const re = /@media\s*\(max-width:\s*979px\)/g;
  let m;
  while ((m = re.exec(css))) {
    const open = css.indexOf("{", m.index);
    if (open < 0) break;
    let depth = 0;
    let end = open;
    for (let i = open; i < css.length; i++) {
      if (css[i] === "{") depth++;
      else if (css[i] === "}") {
        depth--;
        if (depth === 0) { end = i; break; }
      }
    }
    blocks.push(css.slice(open + 1, end));
  }
  return blocks.join("\n");
}

// JS 侧：窄屏模态的焦点四件套
function scanJs(js) {
  const problems = [];
  const setOpen = extractFunction(js, "function setInspectorOpen(open) {");
  if (!setOpen) {
    problems.push("library.js 里抽不到 setInspectorOpen");
    return problems;
  }
  const body = stripComments(setOpen);

  // D1：关闭态必须不可聚焦（inert）
  const inertSync = extractFunction(js, "function syncInspectorInert() {");
  if (!inertSync) {
    problems.push("library.js 里没有 syncInspectorInert：窄屏关着的抽屉还能被键盘聚焦");
  } else {
    const inertBody = stripComments(inertSync);
    if (!/\.inert\s*=/.test(inertBody)) {
      problems.push("syncInspectorInert 没有设置 inert");
    }
    if (!/aria-hidden/.test(inertBody)) {
      problems.push("syncInspectorInert 没有同步 aria-hidden：读屏还是会念屏外的内容");
    }
  }
  if (!/syncInspectorInert\s*\(\s*\)/.test(body)) {
    problems.push("setInspectorOpen 没有同步 inert：抽屉开合后状态对不上");
  }
  const inertCalls = (js.match(/syncInspectorInert\s*\(\s*\)/g) || []).length;
  if (inertCalls < 3) {
    problems.push(`syncInspectorInert 只被调用了 ${inertCalls} 次：页面刚打开和窗口改宽度时都没同步`);
  }

  // D2：打开时焦点要进抽屉
  if (!/focusInspectorPanel\s*\(/.test(body)) {
    problems.push("setInspectorOpen 打开时没有把焦点移进抽屉（focusInspectorPanel）");
  }
  // D4：关闭时焦点要还给触发按钮
  if (!/inspectorToggle\??\.focus\s*\(/.test(body)) {
    problems.push("setInspectorOpen 关闭时没有把焦点还给 #inspectorToggle");
  }
  // D3：抽屉里要有焦点循环
  const trap = extractFunction(js, "function trapInspectorFocus(event) {");
  if (!trap) {
    problems.push("library.js 里没有 trapInspectorFocus：抽屉打开时 Tab 会跑到背后的题目上");
  } else {
    const trapBody = stripComments(trap);
    if (!/"Tab"|'Tab'/.test(trapBody)) {
      problems.push("trapInspectorFocus 没有处理 Tab");
    }
    if (!/preventDefault/.test(trapBody)) {
      problems.push("trapInspectorFocus 没有阻止默认行为，焦点还是会跑出去");
    } else if ((trapBody.match(/preventDefault/g) || []).length < 2) {
      // Tab 和 Shift+Tab 两条分支都得拦：只拦一条，另一条照样能把焦点甩出去。
      problems.push("trapInspectorFocus 只在一条分支上阻止默认行为，另一条仍会把焦点甩出抽屉");
    }
    if (!/contains\s*\(/.test(trapBody)) {
      problems.push("trapInspectorFocus 没有判断焦点是否还在抽屉里");
    }
  }
  if (!/trapInspectorFocus\s*\(/.test(js)) {
    problems.push("trapInspectorFocus 从来没被调用过：焦点循环形同虚设");
  }
  return problems;
}

// CSS 侧：别把抽屉改回 visibility 方案
function scanCss(css) {
  const problems = [];
  const narrow = narrowBlocks(css);
  if (!narrow.trim()) {
    problems.push("library.css 里找不到 @media (max-width: 979px) 窄屏块");
    return problems;
  }
  const flat = narrow.replace(/\s+/g, " ");
  const hidesInspector = /\.question-inspector[^{]*\{[^}]*visibility:\s*hidden/.test(flat);
  if (hidesInspector) {
    problems.push("窄屏抽屉改用 visibility: hidden：打开时 visibility 还停在过渡起点，"
      + "focus() 会被拒，焦点进不去抽屉");
  }
  return problems;
}

function readBoth() {
  return {
    js: fs.readFileSync(LIB_JS, "utf8"),
    css: fs.readFileSync(LIB_CSS, "utf8"),
  };
}

/* ---------- 尺子自检 ---------- */

test("尺子自检：必须抽得到 setInspectorOpen", () => {
  const { js, css } = readBoth();
  assert.ok(extractFunction(js, "function setInspectorOpen(open) {"), "抽不到 setInspectorOpen");
  assert.ok(narrowBlocks(css).trim(), "取不到窄屏媒体查询块");
});

test("尺子自检：把焦点逻辑抠掉，扫描器必须报出问题（否则断言恒真）", () => {
  const { js, css } = readBoth();
  const brokenJs = js
    .replace(/focusInspectorPanel\s*\([^)]*\)\s*;?/g, "")
    .replace(/ui\.inspectorToggle\??\.focus\s*\([^)]*\)\s*;?/g, "")
    .replace(/panel\.inert\s*=\s*[^;]+;/g, "")
    .replace(/function trapInspectorFocus\(event\) \{[\s\S]*?\n {2}\}\n/, "");
  // CSS 侧：往窄屏块里注入一条 visibility: hidden 的老写法，
  // 扫描器必须把它抓出来（当前 CSS 里本来就没有这条，替换式变异抓不到）。
  const brokenCss = css.replace(
    "@media (max-width: 979px) {",
    "@media (max-width: 979px) { .question-inspector { visibility: hidden; }",
  );
  assert.notStrictEqual(brokenJs, js, "抠 JS 的补丁没生效");
  assert.ok(scanJs(brokenJs).length >= 4, "抠掉焦点逻辑后扫描器没报够问题，断言恒真");
  assert.ok(scanCss(brokenCss).length >= 1, "visibility 方案没被抓出来，断言恒真");
});

/* ---------- 缺陷本体 ---------- */

test("D1：窄屏关闭态的抽屉必须不可聚焦（inert）", () => {
  const problems = scanJs(readBoth().js).filter((p) => /inert/.test(p));
  assert.deepStrictEqual(problems, [], problems.join("\n"));
});

test("D2：打开抽屉时焦点必须进抽屉", () => {
  const problems = scanJs(readBoth().js).filter((p) => /焦点移进抽屉/.test(p));
  assert.deepStrictEqual(problems, [], problems.join("\n"));
});

test("D3：抽屉打开时焦点必须困在抽屉里", () => {
  const problems = scanJs(readBoth().js).filter((p) => /trapInspectorFocus|焦点循环/.test(p));
  assert.deepStrictEqual(problems, [], problems.join("\n"));
});

test("D4：关掉抽屉后焦点必须回到触发按钮", () => {
  const problems = scanJs(readBoth().js).filter((p) => /焦点还给/.test(p));
  assert.deepStrictEqual(problems, [], problems.join("\n"));
});

test("D5：别把抽屉改回 visibility 方案（会挡住打开时的聚焦）", () => {
  const problems = scanCss(readBoth().css);
  assert.deepStrictEqual(problems, [], problems.join("\n"));
});
