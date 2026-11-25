"use strict";
// AI 建议区应对齐「标签 / 常用」的安静分区，不再用绿底高亮卡和闪电图标。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

describe("Eagle AI 建议安静化", () => {
  it("1. 去掉绿底高亮卡与闪电图标标题", () => {
    assert.match(html, /id="aiTagSuggestSection"/);
    assert.match(html, /class="[^"]*eagle-ai-suggest/);
    assert.match(html, />AI 建议</);
    assert.doesNotMatch(html, /border-emerald-200\/80 bg-emerald-50\/35/);
    assert.doesNotMatch(
      html,
      /font-bold text-emerald-900[\s\S]{0,120}AI 标签建议/
    );
    // 标题旁不再挂闪电 SVG
    const section = html.slice(
      html.indexOf('id="aiTagSuggestSection"'),
      html.indexOf('id="questionMetaCard"')
    );
    assert.doesNotMatch(section, /M13 10V3L4 14h7v7l9-11h-7z/);
    assert.doesNotMatch(section, /⚡/);
  });

  it("2. 后台补标是图标按钮，汉字只进自定义悬浮气泡", () => {
    assert.match(html, /\.eagle-ai-bg-toggle/);
    assert.match(html, /id="aiBgTagToggleBtn"/);
    assert.match(html, /function showEagleHoverTip/);
    assert.match(html, /#eagleHoverTip/);
    assert.match(html, /data-tip-title/);
    assert.match(html, /function setEagleTip\(/);
    assert.match(html, /setEagleTip\(btn, tipTitle, tipBody\)/);
    assert.doesNotMatch(html, />后台补标 · (开|关)</);
    const section = html.slice(
      html.indexOf('id="aiTagSuggestSection"'),
      html.indexOf('id="questionMetaCard"')
    );
    assert.match(section, /<svg /);
    assert.doesNotMatch(section, /\stitle="/);
  });

  it("3. 生成建议是可感知的实体按钮，悬停有动效", () => {
    assert.match(html, /\.eagle-ai-action\.is-primary:hover/);
    assert.match(html, /transform: translateY\(-1px\)/);
    assert.match(html, /eagle-ai-action-ico/);
    assert.match(html, /生成建议/);
    assert.match(html, /class="eagle-ai-action is-primary"/);
  });
});
