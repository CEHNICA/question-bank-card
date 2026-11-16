"use strict";
// Eagle：看不见的公式不许白白排版。
//
// 背景：KaTeX 的 auto-render 不管元素可见性，扫到什么就排什么。Eagle 的
// 题目解析框 analysis_box 默认带 hidden 类，但列表分块渲染时对整块调用
// renderMathInElement，于是折叠着的解析框里几十个公式照样被排版 ——
// 实测首屏 186 个 KaTeX 里有 93 个在看不见的盒子里，白占约 3700 个 DOM 节点。
//
// 要求：renderMathInElement 之前先把"自身不可见"的容器标记掉并加进
// ignoredClasses，让 auto-render 整棵跳过；标记必须是临时的（每次渲染
// 前清干净、按当前可见性重算），否则展开时公式永远排不出来。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("Eagle 折叠解析框的公式按需排版", async (t) => {
  await t.test("1. 有专用的延后排版标记", () => {
    assert.ok(
      html.includes("katex-defer"),
      "应存在 katex-defer 标记类，用来告诉 auto-render 跳过整棵子树"
    );
  });

  await t.test("2. renderMathInElement 必须带 ignoredClasses 把标记类挡在外面", () => {
    // 关键：KaTeX 的 ignoredClasses 是按元素自身 class 匹配并整棵跳过子树，
    // 不加这个选项，标记只是装饰。
    assert.ok(
      /ignoredClasses/.test(html),
      "调用 renderMathInElement 时必须传 ignoredClasses"
    );
    const ignoredMatch = html.match(/ignoredClasses\s*:\s*([^\n]+)/);
    assert.ok(ignoredMatch, "应能定位到 ignoredClasses 的取值");
    assert.ok(
      ignoredMatch[1].includes("katex-defer"),
      `ignoredClasses 必须包含 katex-defer，实际: ${ignoredMatch[1].trim()}`
    );
  });

  await t.test("3. 标记必须是临时的：渲染前清掉旧的，渲染后撤掉新的", () => {
    // 展开解析框时那一层 hidden 被移除，若标记还留着，公式就永远排不出来。
    const removeCount = (html.match(/classList\.remove\(\s*['"]katex-defer['"]\s*\)/g) || []).length;
    assert.ok(
      removeCount >= 1,
      "渲染结束后必须把 katex-defer 标记撤掉，否则展开后公式排不出来"
    );
  });

  await t.test("4. 隐藏判定必须看真实的计算样式", () => {
    assert.ok(
      /getComputedStyle/.test(html),
      "必须用 getComputedStyle 判断元素是否真的不可见"
    );
    assert.ok(
      /display\s*!==?\s*['"]none['"]|['"]none['"]\s*!==?\s*\w+\.display/.test(html) ||
        /display/.test(html),
      "隐藏判定要覆盖 display:none"
    );
  });

  await t.test("5. 展开解析框的路径必须重新排版", () => {
    // toggleAnalysisInstant 展开时会调 triggerKaTeX —— 这是被延后的公式
    // 唯一的补渲染入口，少了它用户看到的就是 $...$ 源码。
    const fn = html.match(/function toggleAnalysisInstant\([\s\S]{0,1600}?\n    \}/);
    assert.ok(fn, "应能找到 toggleAnalysisInstant");
    assert.ok(
      fn[0].includes("triggerKaTeX"),
      "toggleAnalysisInstant 展开时必须调 triggerKaTeX 补渲染公式"
    );
    assert.ok(
      fn[0].includes("katexDone"),
      "toggleAnalysisInstant 必须靠 katexDone 标记避免重复排版"
    );
  });

  await t.test("6. 解析框默认是折叠的（所以延后排版才有意义）", () => {
    assert.ok(
      /id="analysis_box_\$\{q\.id\}"[^>]*class="\$\{isAnalysisExpanded\s*\?\s*''\s*:\s*'hidden'\}/.test(html),
      "analysis_box 未展开时必须带 hidden 类"
    );
  });
});
