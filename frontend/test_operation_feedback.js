// 第二十二轮守护测试：操作失败时的反馈（题库页「点了没反应」链路）。
//
// 背景：这轮把题库页的写操作逐个打挂（故障注入，Playwright 拦截接口返回 500/502），
// 看点击之后界面上有没有话说、有没有说对。实测出两个真缺陷：
//
//   D1 说错了 —— 后端整个挂掉时，组卷预览把篮子里每一道题都标成
//      「这道题当前不能使用」。那是把系统故障说成题目有问题：老师照着这句话
//      把好题全从篮子里移出去。真实原因（连不上服务器 / 读取失败）被吞了。
//   D2 说漏了 —— 后端返回的不是 JSON（网关 502 的 HTML）时，
//      await response.json() 抛一句英文的 "Unexpected token '<'"，
//      那句英文原样冒到 toast 和状态栏上。实测原文：
//      Unexpected token '<', "<html><bod"... is not valid JSON
//
// 修法（注意：不许引入新的模块级 helper —— 既有测试会把 library.js 里的函数
// 抠出来单��在 vm 沙箱里跑，沙箱不带额外依赖）：
//   · 每个写操作就地 .catch(() => { throw new Error("一句人话") })
//   · missingReason 认得「中文自由文本原因」，认不出的英文枚举照旧兜底
//   · openPrint 的网络错误归一就地内联
//
// 这里的断言是**行为级**的：把 library.js 里的函数抠出来用 vm 真跑一遍，
// 不是看源码长相 —— 改了实现但行为没变，不该红；行为变了，必红。
//
// 想自证看门狗有效，可以喂旧版副本：
//   QB_LIB_JS=旧版library.js node --test frontend/test_operation_feedback.js

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const LIB_JS = process.env.QB_LIB_JS || path.join(ROOT, "frontend", "library.js");
const source = fs.readFileSync(LIB_JS, "utf8");

// ── 抠函数：找 `function 名字(`，配平参数表再配平函数体 ──────────────
// 注意：async function 的 "async " 在 "function " 前面，切片时要把它带上，
// 否则抠出来的源码少了 async，跑起来就不是异步函数了。
function extractFunction(name) {
  const idx = source.indexOf(`function ${name}(`);
  if (idx < 0) return null;
  let start = idx;
  const prefix = source.slice(Math.max(0, idx - 6), idx);
  if (prefix.endsWith("async ")) start = idx - 6;
  let i = source.indexOf("(", idx);
  let depth = 0;
  for (; i < source.length; i += 1) {
    if (source[i] === "(") depth += 1;
    else if (source[i] === ")") {
      depth -= 1;
      if (depth === 0) { i += 1; break; }
    }
  }
  const brace = source.indexOf("{", i);
  if (brace < 0) return null;
  let b = 0;
  for (let j = brace; j < source.length; j += 1) {
    if (source[j] === "{") b += 1;
    else if (source[j] === "}") {
      b -= 1;
      if (b === 0) return source.slice(start, j + 1);
    }
  }
  return null;
}

function bodyOf(name) {
  const code = extractFunction(name);
  if (!code) return null;
  const brace = code.indexOf("{", code.indexOf("("));
  return code.slice(brace);
}

const REASONS = source.match(/const MISSING_REASONS = \{[\s\S]*?\};/);

// ── 把 missingReason 抠出来真跑 ────────────────────────────────────────
function loadMissingReason() {
  const parts = [extractFunction("hasChinese"), REASONS ? REASONS[0] : null,
    extractFunction("missingReason")];
  if (parts.some((p) => !p)) return null;
  const code = `${parts.join("\n")}\n;({ missingReason })`;
  return vm.runInNewContext(code, {});
}

// ── T0 尺子自检：抠得出东西吗？抠不出就红，免得后面全是恒真断言 ────────
test("T0 尺子自检：能从 library.js 里抠出 hasChinese / MISSING_REASONS / missingReason", () => {
  for (const name of ["hasChinese", "missingReason"]) {
    const code = extractFunction(name);
    assert.ok(code, `抠不出 ${name}`);
    assert.ok(code.length > 20, `${name} 抠出来的源码太短：${code.length}`);
  }
  assert.ok(REASONS, "找不到 MISSING_REASONS");
  const helpers = loadMissingReason();
  assert.ok(helpers, "missingReason 没能加载起来");
  assert.strictEqual(typeof helpers.missingReason, "function");
  // 抠出来的东西必须自己就能跑 —— 抠错了（少了依赖）要立刻红
  assert.strictEqual(helpers.missingReason({ reason: "superseded" }), "题面改过，这道题已经有新版本了");
});

// ── T1 missingReason：自由文本原因要照原样说出来，英文枚举照旧兜底 ─────
test("T1 missingReason：后端枚举翻成中文，中文原因原样输出，英文枚举兜底", () => {
  const { missingReason } = loadMissingReason();
  // 后端状态枚举
  assert.strictEqual(missingReason({ reason: "superseded" }), "题面改过，这道题已经有新版本了");
  assert.strictEqual(missingReason({ reason: "withdrawn" }), "这道题已撤回");
  assert.strictEqual(missingReason({ reason: "not_found" }), "这道题已不存在");
  // 前端自己塞的一句人话（读取失败、连不上服务器）—— 这一条就是 D1 的看门狗
  assert.strictEqual(missingReason({ reason: "选题读取失败，请重试" }), "选题读取失败，请重试");
  assert.strictEqual(missingReason({ reason: "连不上服务器，请检查网络后重试" }),
    "连不上服务器，请检查网络后重试");
  assert.strictEqual(missingReason({ reason: "题目未返回，请重试" }), "题目未返回，请重试");
  // 认不出来的英文枚举仍然兜底：界面上不该出现英文
  assert.strictEqual(missingReason({ reason: "who_knows" }), "这道题当前不能使用");
  // message 优先于 reason
  assert.strictEqual(missingReason({ reason: "superseded", message: "已经换成新版本了" }), "已经换成新版本了");
  // 没有信息时才用兜底
  assert.strictEqual(missingReason(null), "这道题当前不能使用");
  assert.strictEqual(missingReason({}), "这道题当前不能使用");
});

// ── T2 关键写操作的响应解析都要有中文兜底 ─────────────────────────────
test("T2 写操作解析响应失败时抛中文兜底，不会把英文解析错误抛到界面上", () => {
  const spots = [
    ["copyQuestionImage", "题目图片没有生成，请重试"],
    ["batchItems", "选题读取失败，请重试"],
    ["withdrawItem", "撤回失败"],
    ["withdrawSelected", "撤回失败"],
    ["saveDraft", "草稿未保存"],
  ];
  for (const [name, fallback] of spots) {
    const body = bodyOf(name);
    assert.ok(body, `抠不出 ${name} 的函数体`);
    assert.ok(!/await\s+response\.json\(\)\s*;/.test(body),
      `${name} 里 response.json() 没有任何保护，后端返回非 JSON 时会把英文错误抛到界面上`);
    assert.ok(body.includes(`throw new Error("${fallback}")`),
      `${name} 里没有「${fallback}」这句中文兜底`);
  }
  // 草稿那几处：列表 / 打开单份 / 删除
  const drafts = bodyOf("openDrafts");
  assert.ok(drafts, "抠不出 openDrafts 的函数体");
  assert.ok(drafts.includes('throw new Error("草稿读取失败")'), "草稿读取没有中文兜底");
  assert.ok(drafts.includes('throw new Error("草稿删除失败")'), "草稿删除没有中文兜底");
});

// ── T3 组卷预览整批读失败时，原因要落到每一道题上 ─────────────────────
test("T3 openPrint 整批读失败：把真实原因带给每一道题，而不是「这道题不能用」", () => {
  const body = bodyOf("openPrint");
  assert.ok(body, "抠不出 openPrint 的函数体");
  assert.ok(/failed to fetch/i.test(body), "openPrint 没有把 fetch 的英文报错换掉");
  assert.ok(/message:\s*reason/.test(body),
    "openPrint 造的 missing 条目没有带 message 字段，原因传不到题面上");
  assert.ok(!/reason:\s*error\.message\s*\|\|\s*"读取失败，请重试"/.test(body),
    "openPrint 还在用老写法，原因会被 missingReason 压成「这道题当前不能使用」");
});

// ── T4 行为级：真把函数抠出来跑，喂一个「响应不是 JSON」的后端 ─────────
test("T4 行为级：后端返回 HTML（不是 JSON）时，界面上拿到的是中文而不是英文", async () => {
  const htmlError = async () => { throw new SyntaxError("Unexpected token '<', \"<html><bod\"... is not valid JSON"); };

  // 复制题目图片：失败时走 toast，要看 toast 收到的话
  const toasts = [];
  const sandbox = {
    toast: (message, kind) => { toasts.push({ message, kind }); },
    fetch: async () => ({ ok: true, status: 502, json: htmlError }),
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  const copyCode = extractFunction("copyQuestionImage");
  assert.ok(copyCode, "抠不出 copyQuestionImage");
  vm.runInContext(copyCode, sandbox);
  await sandbox.copyQuestionImage({ id: "abc" }, { disabled: false, textContent: "复制题目图片" });
  const shown = toasts[toasts.length - 1];
  assert.ok(shown, "复制图片失败后一句提示都没有");
  assert.strictEqual(shown.message, "题目图片没有生成，请重试");
  assert.ok(!/unexpected token|is not valid json/i.test(shown.message), "英文解析错误漏到界面上了");

  // 选题读取：没有 catch，错误会往上抛，抛的必须是中文
  const sandbox2 = { fetch: async () => ({ ok: true, status: 502, json: htmlError }) };
  sandbox2.window = sandbox2;
  vm.createContext(sandbox2);
  const batchCode = extractFunction("batchItems");
  assert.ok(batchCode, "抠不出 batchItems");
  vm.runInContext(batchCode, sandbox2);
  await assert.rejects(
    () => sandbox2.batchItems(["q1"]),
    (error) => {
      assert.strictEqual(error.message, "选题读取失败，请重试");
      assert.ok(!/unexpected token|is not valid json/i.test(error.message), "英文解析错误漏到界面上了");
      return true;
    },
  );
});
