"use strict";
// 快速审题弹窗：「指针移开 → 压暗，只留题干」。
//
// 用户要的是：鼠标停在界面上时一切正常显示；鼠标移开这个界面后，
// 除了题目，其余字面全部隐藏，并且把亮度降下来 —— 只是显示题目的感觉。
//
// 三档状态：
//   指针在白色卡片内 → 全亮（徽标、题名、标签、解析区、底部按钮都在）
//   指针移出卡片     → 非题干区淡出到近乎不可见 + 整体轻微降亮度，题干保持全亮可读
//   触屏（hover:none）→ 永远全亮，否则手机上开屏就是一片灰，像坏了
//
// 判定必须锚在白色卡片（.ql-panel）而不是遮罩容器：
// 遮罩容器是 inset-0 铺满视口，用它判 :hover 的话指针停在遮罩上仍算
// 「在界面上」，压暗永远不触发。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const INDEX_HTML_PATH = path.join(__dirname, "eagle", "index.html");
const html = fs.readFileSync(INDEX_HTML_PATH, "utf8");

function quickLookSection() {
  const start = html.indexOf('id="quickLookModal"');
  assert.ok(start > -1, "应包含 quickLookModal 弹窗");
  const end = html.indexOf('id="originalPaperModal"', start);
  assert.ok(end > start, "应找到 quickLookModal 结束边界");
  return html.slice(start, end);
}

test("快速审题：指针移开后压暗只留题干", async (t) => {
  const section = quickLookSection();

  await t.test("1. 白色卡片带 ql-panel 钩子，三块非题干区带 ql-chrome 钩子", () => {
    assert.ok(
      /class="[^"]*\bql-panel\b/.test(section),
      "白色卡片应有 ql-panel 钩子（宽度 + 悬停判定的锚点）"
    );
    const chromeCount = (section.match(/\bql-chrome\b/g) || []).length;
    assert.ok(
      chromeCount >= 3,
      `头部 / 题名行 / 底部条三块都应有 ql-chrome 钩子（实际 ${chromeCount} 处）`
    );
  });

  await t.test("2. 存在 is-idle 压暗规则，同时淡出 opacity 与 filter", () => {
    assert.ok(
      /#quickLookModal\.is-idle \.ql-chrome[^{]*\{[^}]*opacity: 0\.06;[^}]*\}/.test(html),
      "is-idle 时非题干区应淡出到近乎不可见"
    );
    assert.ok(
      /#quickLookModal\.is-idle \.ql-chrome[^{]*\{[^}]*filter: blur\(1px\) brightness\(0\.7\);[^}]*\}/.test(html),
      "is-idle 时非题干区应轻微模糊并降亮度"
    );
  });

  await t.test("2b. 题干区不得挂任何滤镜 —— 题干像素必须零衰减", () => {
    // 踩过的坑：把 brightness 加在 .ql-panel 上想让整卡沉下去，
    // 但 CSS 滤镜没法让子元素反向提亮 → 题干跟着被压了 12%。
    // 现在降亮度只加在非题干区，「画面沉下去」改由遮罩加深实现。
    // 扫规则前先剥掉 /* */ 注释，否则本文件的说明文字会被当成规则误伤。
    const cssRules = html.replace(/\/\*[\s\S]*?\*\//g, "");
    // 逐条规则切开看（跨规则匹配会把后面规则的 brightness 一并吞进来）
    const ruleBlocks = cssRules.match(/[^{}]+\{[^{}]*\}/g) || [];
    for (const rule of ruleBlocks) {
      const selector = rule.split("{")[0];
      const body = rule.slice(selector.length);
      // 只看「宿主元素」是不是面板本身：选择器最后一段（去掉伪类/状态后）
      // 必须是 .ql-panel 才会命中面板。:has(.ql-panel:hover) 里出现
      // .ql-panel 只是「面板内部有没有被 hover」，不等于给面板上滤镜。
      const lastPart = selector
        .split(",")
        .map((s) => s.trim())
        .filter((s) => s.includes(".ql-panel"))
        .map((s) => s.split(/[\s:>+~]+/).filter(Boolean).pop())
        .filter(Boolean);
      if (lastPart.some((part) => part === ".ql-panel")) {
        assert.ok(
          !/brightness/.test(body),
          `直接命中面板的规则不应带 brightness（题干在面板内部）：${selector.trim()}`
        );
      }
    }
    assert.ok(
      ruleBlocks.some((r) => r.includes(".ql-panel") && /max-width:\s*1200px/.test(r)),
      "应有给 .ql-panel 设 max-width: 1200px 的规则"
    );
    // 压暗改为遮罩加深
    assert.ok(
      /#quickLookModal\.is-idle\.ql-backdrop[^{]*\{[^}]*background-color: rgb\(15 23 42 \/ 0\.88\);[^}]*\}/.test(html),
      "is-idle 时遮罩应加深到 0.88（周围暗、题目亮）"
    );
    assert.ok(
      /#quickLookModal\.ql-backdrop:not\(:has\(\.ql-panel:hover\)\)[^{]*\{[^}]*background-color/.test(html),
      ":has() 兜底也应对遮罩加深生效"
    );
  });

  await t.test("3. 题干容器不在淡出规则里（题干必须保持可读）", () => {
    // 题干正文所在的 section 与 #qlContentContainer 都不能被淡出
    assert.ok(
      !/#quickLookModal\.is-idle #qlContentContainer/.test(html),
      "题干容器不应出现在淡出规则里"
    );
    const idleBlocks = html.match(
      /#quickLookModal\.is-idle[^{]*\{[^}]*\}/g
    ) || [];
    for (const block of idleBlocks) {
      assert.ok(
        !block.includes("qlContentContainer"),
        `淡出规则不应命中题干容器：${block.slice(0, 80)}`
      );
    }
    assert.ok(
      /#quickLookModal\.is-idle #qlAnalysisContainer/.test(html),
      "答案与解析区属于「非题干」，应一并淡出"
    );
  });

  await t.test("4. :has() 兜底规则存在（JS 失效时仍能压暗）", () => {
    assert.ok(
      /#quickLookModal:not\(:has\(\.ql-panel:hover\)\)/.test(html),
      "应有 :has() 兜底规则，判定锚在白色卡片而非遮罩容器"
    );
  });

  await t.test("4b. 遮罩 class 挂在 #quickLookModal 自身，选择器不能写成后代", () => {
    // ql-backdrop 与 #quickLookModal 是同一个元素（它既是遮罩又是容器），
    // 写成 #quickLookModal .ql-backdrop 会匹配不到任何东西 —— 静默失效。
    assert.ok(
      /id="quickLookModal" class="[^"]*\bql-backdrop\b/.test(html),
      "ql-backdrop 应挂在 #quickLookModal 自身上"
    );
    assert.ok(
      /#quickLookModal\.ql-backdrop/.test(html),
      "遮罩规则应用 #quickLookModal.ql-backdrop（复合选择器），不是后代选择器"
    );
    // 只查真正的 CSS 规则（剥掉 /* */ 注释），否则会误伤本文档里举的反例
    const cssRules = html.replace(/\/\*[\s\S]*?\*\//g, "");
    assert.ok(
      !/#quickLookModal \.ql-backdrop/.test(cssRules),
      "不应出现 #quickLookModal .ql-backdrop 后代选择器（匹配不到，规则静默失效）"
    );
  });

  await t.test("5. 触屏（hover: none）强制全亮，不压暗", () => {
    // 仓库里已有一个 @media (hover: none) 块（.qc-evidence-btn），
    // 这里要定位到覆盖 ql-panel 的那个，别抓错块。
    const hoverNoneBlocks = [];
    let from = 0;
    for (;;) {
      const at = html.indexOf("@media (hover: none)", from);
      if (at < 0) break;
      hoverNoneBlocks.push(html.slice(at, at + 600));
      from = at + 1;
    }
    assert.ok(hoverNoneBlocks.length > 0, "应有 @media (hover: none) 媒体查询");
    // 面板不再挂滤镜，这里找的是覆盖 ql-chrome 的块（另有覆盖 ql-backdrop 的规则）
    const block = hoverNoneBlocks.find((b) => b.includes("#quickLookModal .ql-chrome"));
    assert.ok(
      block,
      "应有覆盖 #quickLookModal .ql-chrome 的 hover:none 块"
    );
    assert.ok(
      /opacity: 1 !important/.test(block),
      "hover:none 时必须强制 opacity: 1，否则手机上开屏就是一片灰"
    );
    assert.ok(
      /filter: none !important/.test(block),
      "hover:none 时必须去掉压暗滤镜"
    );
    assert.ok(
      /#quickLookModal\.ql-backdrop/.test(block),
      "hover:none 时遮罩应回到原来的不透明度"
    );
  });

  await t.test("6. 尊重系统的「减少动态效果」设置", () => {
    const reducedIdx = html.indexOf("@media (prefers-reduced-motion: reduce)");
    assert.ok(reducedIdx > -1, "应有 prefers-reduced-motion 媒体查询");
    const block = html.slice(reducedIdx, reducedIdx + 600);
    assert.ok(
      /transition: none !important/.test(block),
      "减少动效时应去掉过渡动画"
    );
  });

  await t.test("7. JS 挂 pointerenter/pointerleave 到白色卡片并切 is-idle", () => {
    const scriptMatches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
    const mainScript = scriptMatches[scriptMatches.length - 1][1];

    assert.ok(
      mainScript.includes("function installQuickLookIdleHover"),
      "应定义 installQuickLookIdleHover"
    );
    const fnStart = mainScript.indexOf("function installQuickLookIdleHover");
    const fnEnd = mainScript.indexOf("function openQuickLookModal", fnStart);
    assert.ok(fnStart > -1 && fnEnd > fnStart, "应找到 installQuickLookIdleHover 函数体");
    const fnBody = mainScript.slice(fnStart, fnEnd);

    assert.ok(
      /panel\.addEventListener\('pointerenter'/.test(fnBody),
      "应在白色卡片上挂 pointerenter"
    );
    assert.ok(
      /panel\.addEventListener\('pointerleave'/.test(fnBody),
      "应在白色卡片上挂 pointerleave"
    );
    assert.ok(
      /classList\.toggle\('is-idle'/.test(fnBody),
      "应通过 classList 切换 is-idle（状态唯一真源 = DOM class）"
    );
    assert.ok(
      fnBody.includes(".ql-panel"),
      "监听应挂在 .ql-panel 上（判定锚点不能是铺满视口的遮罩容器）"
    );
    assert.ok(
      fnBody.includes("pointerType") && fnBody.includes("mouse"),
      "只认鼠标指针：触摸的 enter/leave 语义不可靠"
    );
  });

  await t.test("8. 打开与关闭都会清掉 is-idle（不残留压暗态）", () => {
    const scriptMatches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
    const mainScript = scriptMatches[scriptMatches.length - 1][1];

    const openStart = mainScript.indexOf("function openQuickLookModal");
    const openEnd = mainScript.indexOf("function closeQuickLookModal", openStart);
    const openBody = mainScript.slice(openStart, openEnd);
    assert.ok(
      openBody.includes("classList.remove('is-idle')"),
      "打开时应清掉 is-idle，保证首次显示是全亮"
    );
    assert.ok(
      openBody.includes("installQuickLookIdleHover()"),
      "打开时应安装悬停监听"
    );

    const closeStart = mainScript.indexOf("function closeQuickLookModal");
    const closeEnd = mainScript.indexOf("function navQuickLook", closeStart);
    const closeBody = mainScript.slice(closeStart, closeEnd);
    assert.ok(
      closeBody.includes("classList.remove('is-idle')"),
      "关闭时应清掉 is-idle，否则下次打开会带着残态"
    );
  });

  await t.test("9. 切题不重置压暗态（指针位置没变，与解析显隐无关）", () => {
    const scriptMatches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
    const mainScript = scriptMatches[scriptMatches.length - 1][1];
    const navStart = mainScript.indexOf("function navQuickLook");
    const navEnd = mainScript.indexOf("function toggleQuickLookAnalysis", navStart);
    const navBody = mainScript.slice(navStart, navEnd);
    assert.ok(
      !navBody.includes("is-idle"),
      "切题不该动压暗态：压暗与指针位置有关，与解析显隐是两套独立机制"
    );
    assert.ok(
      navBody.includes("quickLookShowAnalysis = false"),
      "切题仍应重置答案与解析的显隐（原有行为不变）"
    );
  });

  await t.test("10. 主内联脚本仍能通过语法编译", () => {
    const scriptMatches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
    const mainScript = scriptMatches[scriptMatches.length - 1][1];
    assert.doesNotThrow(
      () => new vm.Script(mainScript),
      "eagle/index.html 主内联脚本应无语法错误"
    );
  });

  await t.test("11. 题干字号放大到 17px，解析 15px", () => {
    // 弹窗加宽到 1200px 后，15px 的题干在宽屏上偏小。
    assert.ok(
      /#quickLookModal \.ql-question-body \{[^}]*font-size: 17px;/.test(html),
      "题干字号应为 17px"
    );
    assert.ok(
      /#quickLookModal \.ql-sol-content \{[^}]*font-size: 15px;/.test(html),
      "解析区字号应为 15px（与题干拉开一级层次）"
    );
    // 行高保持不变：公式多的题靠行高呼吸，字号一加就容易挤成一坨
    assert.ok(
      /#quickLookModal \.ql-question-body \{[^}]*line-height: 1\.85;/.test(html),
      "题干行高应保持 1.85"
    );
  });
});
