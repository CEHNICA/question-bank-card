// 第二十五轮：中文输入法（IME）组合态。
//
// 这轮体检在题库页实测出一条真缺陷：
//   搜索框的 input 监听完全不管中文输入法的组合态。拼音还没上屏时就会触发 input，
//   页面拿拼音串去搜 —— 实测抓到的请求是 q=fangcheng，地址栏被写成 ?q=fangcheng，
//   列表刷成「0 条结果」，等用户上屏才又搜出 25 条。
//   老师搜题目全靠中文拼音，这个坑每天都会踩到。
//
// 项目里另一个搜索框（标签管理器）早就是标准写法（input 时 if (isComposing) return，
// compositionend 再搜），题库页搜索框漏了 —— 这轮把它对齐。
//
// 守护测试是静态源码断言；实测那部分由 _audit_ime.py 负责。
// 用 QB_LIB_JS 喂旧版本可以自证看门狗有效。

const path = require("node:path");
const fs = require("node:fs");
const test = require("node:test");
const assert = require("node:assert/strict");

const ROOT = path.resolve(__dirname, "..");
function read(envName, rel) {
  return fs.readFileSync(process.env[envName] || path.join(ROOT, rel), "utf8");
}
const libJs = read("QB_LIB_JS", "frontend/library.js");

// 从某个位置起，抓到配平的 addEventListener(...) 块结尾。
function handlerFrom(src, start) {
  let depth = 0;
  let began = false;
  for (let i = start; i < src.length && i < start + 4000; i++) {
    if (src[i] === "(") { depth++; began = true; }
    else if (src[i] === ")") {
      depth--;
      if (began && depth === 0) return src.slice(start, i + 1);
    }
  }
  return "";
}

// 所有 addEventListener("input", ...) 处理器的位置。
function inputHandlers(src) {
  const out = [];
  const re = /addEventListener\(\s*"input"/g;
  let m;
  while ((m = re.exec(src))) out.push(m.index);
  return out;
}

// 搜索框那个 input 处理器（ui.search，不是别处的同名）。
function searchInputHandler(src) {
  const at = src.indexOf('ui.search.addEventListener("input"');
  if (at < 0) return null;
  return handlerFrom(src, at);
}

// ── 0. 尺子自检 ────────────────────────────────────────────────────
test("尺子自检：能找到搜索框的 input 处理器", () => {
  const h = searchInputHandler(libJs);
  assert.ok(h, "尺子自检：library.js 里找不到 ui.search 的 input 监听（改名了？这条断言要重写）");
  assert.ok(inputHandlers(libJs).length >= 3,
    "尺子自检：全文件只扫到 " + inputHandlers(libJs).length + " 个 input 监听，尺子本身可能坏了");
  // 反向自检：查找器对不存在的东西必须返回空。
  assert.equal(searchInputHandler(libJs.replace('ui.search.addEventListener("input"', 'ui.nope.addEventListener("input"')), null,
    "尺子自检：查找器对不存在的监听也返回了东西");
});

// ── 1. 组合态期间不能拿拼音去搜 ────────────────────────────────────
test("输入法：搜索框在组合态期间不搜索", () => {
  const h = searchInputHandler(libJs);
  assert.ok(h, "尺子自检：找不到搜索框的 input 监听");
  assert.match(h, /isComposing|searchComposing/,
    "搜索框完全不管中文输入法的组合态：拼音还没上屏就会拿拼音去搜，"
    + "实测请求是 q=fangcheng、地址栏被写成 ?q=fangcheng、列表刷成 0 条");
  assert.match(h, /return/,
    "组合态要提前 return，把这次 input 挡掉");
});

// ── 2. 要有人开始/结束组合态 ───────────────────────────────────────
test("输入法：搜索框跟踪 composition 的开始和结束", () => {
  assert.match(libJs, /ui\.search\.addEventListener\(\s*"compositionstart"/,
    "搜索框没监听 compositionstart：组合态无从知道");
  assert.match(libJs, /ui\.search\.addEventListener\(\s*"compositionend"/,
    "搜索框没监听 compositionend：拼音上屏后不会重新搜");
});

// ── 3. 拼音上屏后要重新搜一次 ──────────────────────────────────────
test("输入法：拼音上屏后重新搜一次", () => {
  const at = libJs.indexOf('ui.search.addEventListener("compositionend"');
  assert.ok(at >= 0, "尺子自检：找不到搜索框的 compositionend");
  const block = handlerFrom(libJs, at);
  assert.match(block, /scheduleSearch|state\.q\s*=/,
    "拼音上屏后没人重新搜：有些浏览器上屏后不再派发 input，"
    + "组合态被挡掉的那次搜索就永远补不回来了");
});

// ── 4. 任何「input 触发搜索」的处理器都得防组合态 ──────────────────
test("输入法：凡是 input 就发请求的处理器，都得防组合态", () => {
  const offenders = [];
  for (const at of inputHandlers(libJs)) {
    const h = handlerFrom(libJs, at);
    if (!/\b(load|fetch|syncUrl)\s*\(/.test(h)) continue;   // 只看会发请求/写 URL 的
    if (/isComposing|composing/.test(h)) continue;          // 已经防了
    offenders.push(h.split("\n")[0].trim());
  }
  assert.equal(offenders.length, 0,
    "这些输入框在组合态期间就会发请求/写地址栏，中文拼音会被当关键词送出去：\n  "
    + offenders.join("\n  "));
});
