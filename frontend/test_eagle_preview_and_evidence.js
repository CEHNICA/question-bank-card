"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const eagleHtmlPath = path.resolve(__dirname, "eagle/index.html");
const eagleHtml = fs.readFileSync(eagleHtmlPath, "utf8");

test("Eagle 工作台：题卡编辑 LaTeX 光标联动 DOM 与样式规范", () => {
  // 1. 样式规范：包含 Devin 规范的光标、选区、公式高亮及原卷选框类名
  assert.ok(eagleHtml.includes(".qb-preview-caret"), "包含光标高亮样式");
  assert.ok(eagleHtml.includes(".qb-preview-active-formula"), "包含公式激活高亮样式");
  assert.ok(eagleHtml.includes(".qb-preview-active-symbol"), "包含符号激活高亮样式");
  assert.ok(eagleHtml.includes(".source-box.text"), "包含原卷题干文字橙色选框样式");
  assert.ok(eagleHtml.includes(".source-box.figure"), "包含原卷配图绿色选框样式");

  // 2. 编辑题卡弹窗：包含提示条和相对定位的预览容器
  assert.ok(eagleHtml.includes('id="editLivePreviewHint"'), "包含编辑实时联动提示栏");
  assert.ok(eagleHtml.includes('id="editLivePreviewBox"'), "包含实时排版预览容器");
  assert.match(eagleHtml, /id="editLivePreviewBox"[^>]*class="[^"]*relative/, "预览容器必须具备 relative 定位以容纳绝对定位光标");

  // 3. JS 逻辑：调用 QB.renderQuestion 与 trackSource
  assert.ok(eagleHtml.includes("QB.renderQuestion"), "必须使用既有排版引擎 QB.renderQuestion 渲染");
  assert.ok(eagleHtml.includes("trackSource: true"), "必须启用 trackSource 追踪源码字符偏移");
  assert.ok(eagleHtml.includes("QB.previewSelection"), "必须调用 QB.previewSelection 同步竖杠光标与公式激活");
  assert.ok(eagleHtml.includes("syncPreviewCursorPosition"), "包含光标同步派发函数");
});

test("Eagle 工作台：原卷核验证据（Ctrl+滚轮缩放、拖拽平移、选框渲染）", () => {
  // 1. 原卷模态框 DOM 结构完整
  assert.ok(eagleHtml.includes('id="originalPaperModal"'), "必须包含原卷模态框");
  assert.ok(eagleHtml.includes('id="opViewport"'), "必须包含画布视口");
  assert.ok(eagleHtml.includes('id="opCanvasContent"'), "必须包含画布容器");
  assert.ok(eagleHtml.includes('id="opPaperImg"'), "必须包含原卷高分辨率图像");
  assert.ok(eagleHtml.includes('id="opBoxLayer"'), "必须包含选框绘制图层");

  // 2. 缩放控制与手势逻辑
  assert.ok(eagleHtml.includes(".ctrlKey"), "必须监听 Ctrl 键以实现按住 Ctrl 滚轮缩放");
  assert.ok(eagleHtml.includes("zoomOp"), "包含缩放核算与视口平稳函数");
  assert.ok(eagleHtml.includes("initOpGestures"), "包含手势与拖拽平移绑定函数");
  assert.ok(eagleHtml.includes("pointerdown"), "包含鼠标按下拖拽平移监听");
  assert.ok(eagleHtml.includes("pointermove"), "包含鼠标平移联动");

  // 3. 选框绘制与整页/本题模式切换
  assert.ok(eagleHtml.includes("renderOriginalPaperBoxes"), "包含选框绘制函数");
  assert.ok(eagleHtml.includes("extractQuestionRegions"), "包含题目切片坐标提取函数");
  assert.ok(eagleHtml.includes("setOpMode"), "包含整页位置与只看本题模式切换");
  assert.ok(eagleHtml.includes("btnOpModePage") && eagleHtml.includes("btnOpModeQuestion"), "包含模式切换按钮");
});
