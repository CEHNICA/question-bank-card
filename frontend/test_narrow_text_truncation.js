"use strict";
// 窄屏说明文字截断的守护测试（第十轮：内容溢出与截断维度）。
//
// 实测发现的真缺陷（Playwright 真实渲染几何，390px 宽度，DOM 几何实测）：
//
//   1) 标签管理弹窗头部的说明文字
//        <p class="text-[11px] text-slate-400">按群组查看真实标签目录，可新建标签、
//        启用/停用；标签归并请走题库设置</p>
//      窄屏媒体查询里有一条 `.tm-head-text p { white-space: nowrap; ... ellipsis }`
//      把整句压成一行省略号，clientW=284 / scrollW=367，后半句
//      「可新建标签、启用/停用；标签归并请走题库设置」永远看不见。
//      它既没有 title、也没有详情入口 -> 用户没有任何途径看到全文。
//
//   2) 解答题编辑弹窗底部的操作说明
//        <span class="text-slate-400 truncate">保存会写回题库正式解析（……）</span>
//      truncate = nowrap + overflow:hidden + ellipsis，窄屏 390px 下 clientW=190
//      / scrollW=431，同样没有任何全文入口。
//
// 对照：标题类的截断（.op-title）是有意设计（长标题省略号），不算缺陷；
// 自带 title 的元素（悬停可见全文）也不算缺陷 —— 本测试只盯「无任何全文入口」
// 的那一类。
//
// 静态源码断言：喂修复前的 HTML（--html=）进来必须变红。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
}

const eaglePath = arg("html") || path.join(__dirname, "eagle", "index.html");
const eagle = fs.readFileSync(eaglePath, "utf8");

// ---------------------------------------------------------------- 样式解析

function styleBlocks(html) {
  const out = [];
  const re = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
  let m;
  while ((m = re.exec(html))) out.push(m[1]);
  return out;
}

/** 取出所有 @media (max-width: Npx) { ... } 块的正文（括号配平）。 */
function narrowMediaBlocks(cssTexts) {
  const blocks = [];
  for (const text of cssTexts) {
    const re = /@media[^{]*max-width\s*:\s*\d+px[^{]*\{/g;
    let m;
    while ((m = re.exec(text))) {
      let i = m.index + m[0].length;
      let depth = 1;
      const start = i;
      while (i < text.length && depth > 0) {
        if (text[i] === "{") depth += 1;
        else if (text[i] === "}") depth -= 1;
        i += 1;
      }
      blocks.push(text.slice(start, i - 1));
    }
  }
  return blocks;
}

const narrowBlocks = narrowMediaBlocks(styleBlocks(eagle));

/** 在窄屏块里找某选择器的规则正文；找不到返回 null。
 *  selectorSrc 传的是**正则源**（不要二次转义），用 ^\s* + m 标志，
 *  否则规则前面是换行 + 缩进时会一条都匹配不到，断言静默失效。 */
function narrowRule(selectorSrc) {
  const re = new RegExp("^\\s*" + selectorSrc + "\\s*\\{([^}]*)\\}", "m");
  for (const block of narrowBlocks) {
    const m = block.match(re);
    // 只有一个捕获组，正文在 m[1]。（曾写成 m[2] -> undefined，
    // 断言恒真、整条看门狗形同虚设；下面那条自检就是防这个。）
    if (m) return m[1];
  }
  return null;
}

// ---------------------------------------------------------------- 断言

it("尺子自检：窄屏规则查找器必须真的能命中（防断言恒真）", () => {
  // .tm-head-text 是窄屏媒体查询里确定存在的规则。
  // 如果这里拿不到，说明查找器本身坏了（比如取错捕获组），
  // 那下面两条断言会变成"永远通过"的假看门狗，必须先红。
  const probe = narrowRule("\\.tm-head-text");
  assert.ok(
    typeof probe === "string" && probe.length > 0,
    "窄屏规则查找器失效：连 .tm-head-text 这种确定存在的规则都取不到，" +
      "断言会静默恒真"
  );
});

it("窄屏下标签管理说明文字不得被单行截断", () => {
  const rule = narrowRule("\\.tm-head-text\\s+p");
  if (!rule) return; // 没有额外规则 = 默认 normal，正常显示
  assert.ok(
    !/white-space\s*:\s*nowrap/.test(rule),
    "窄屏媒体查询里 .tm-head-text p 不能用 white-space: nowrap，" +
      "说明文字会被压成一行省略号且无全文入口"
  );
  assert.ok(
    !/text-overflow\s*:\s*ellipsis/.test(rule),
    "窄屏媒体查询里 .tm-head-text p 不能用 text-overflow: ellipsis，" +
      "说明文字会被省略号截断且无全文入口"
  );
});

it("统一编辑工作台状态说明文字不得用 truncate 或 nowrap 截断", () => {
  // 旧独立弹窗 #solutionEditorModal 已整合移除；新的统一工作台状态说明与答案提示不得做省略截断
  assert.ok(!eagle.includes('id="solutionEditorModal"'), "旧独立解答题编辑弹窗应已整合移除");
  const unifiedJs = fs.readFileSync(path.join(__dirname, "library-unified-editor.js"), "utf8");
  const editorCss = fs.readFileSync(path.join(__dirname, "question-editor.css"), "utf8");
  assert.ok(unifiedJs.includes('"editor-status-line"'), "统一编辑工作台应包含状态说明行 .editor-status-line");
  assert.ok(unifiedJs.includes('"editor-answer-subtitle"'), "统一编辑工作台应包含答案副标题 .editor-answer-subtitle");
  for (const cls of ["editor-status-line", "editor-answer-subtitle", "editor-position-hint"]) {
    const m = editorCss.match(new RegExp(`\\.${cls}\\s*\\{([^}]*)\\}`));
    if (m) {
      assert.ok(!/white-space\s*:\s*nowrap/.test(m[1]), `.${cls} 不得使用 nowrap 强制单行截断`);
      assert.ok(!/text-overflow\s*:\s*ellipsis/.test(m[1]), `.${cls} 不得使用 ellipsis 隐藏说明文字`);
    }
  }
});

it("窄屏媒体查询里不得存在作用在说明性 p 上的 ellipsis 截断", () => {
  for (const block of narrowBlocks) {
    const re = /([^{}]*\bp\b[^{}]*)\{([^}]*)\}/g;
    let m;
    while ((m = re.exec(block))) {
      const selector = m[1].trim();
      const body = m[2];
      if (!/text-overflow\s*:\s*ellipsis/.test(body)) continue;
      // 标题类（.op-title）是有意设计，不算说明文字
      if (/\.op-title/.test(selector)) continue;
      // .tm-head-text p 由上一条精确断言负责（这里跳过以免同因多红）
      if (/\.tm-head-text/.test(selector)) continue;
      assert.fail(
        `窄屏媒体查询里 "${selector}" 用了 text-overflow: ellipsis，` +
          "说明性文字被截断后没有全文入口"
      );
    }
  }
});
