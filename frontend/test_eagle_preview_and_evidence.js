"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const eagleHtmlPath = path.resolve(__dirname, "eagle/index.html");
const eagleHtml = fs.readFileSync(eagleHtmlPath, "utf8");
const editorCss = fs.readFileSync(path.resolve(__dirname, "question-editor.css"), "utf8");
const editorHelpersJs = fs.readFileSync(path.resolve(__dirname, "qb-editor-helpers.js"), "utf8");
const unifiedEditorJs = fs.readFileSync(path.resolve(__dirname, "library-unified-editor.js"), "utf8");

test("Eagle 工作台：题卡编辑 LaTeX 光标联动 DOM 与样式规范", () => {
  // 1. 样式规范：包含光标、选区、公式高亮及原卷选框类名，预览容器具备 relative 定位
  assert.ok(eagleHtml.includes(".qb-preview-caret"), "包含光标高亮样式");
  assert.ok(eagleHtml.includes(".qb-preview-active-formula"), "包含公式激活高亮样式");
  assert.ok(eagleHtml.includes(".qb-preview-active-symbol"), "包含符号激活高亮样式");
  assert.ok(eagleHtml.includes(".source-box.text"), "包含原卷题干文字橙色选框样式");
  assert.ok(eagleHtml.includes(".source-box.figure"), "包含原卷配图绿色选框样式");
  assert.match(
    editorCss,
    /dialog\.qb-editor-shell\s+\.editor-preview\s*\{[^}]*position:\s*relative/,
    "统一工作台预览容器必须具备 relative 定位以容纳绝对定位光标"
  );

  // 2. 统一编辑工作台：包含联动提示条与实时排版预览容器
  assert.ok(eagleHtml.includes('/question-editor.css'), "Eagle 页必须引入统一编辑工作台样式");
  assert.ok(eagleHtml.includes('/library-unified-editor.js'), "Eagle 页必须引入统一编辑工作台脚本");
  assert.ok(unifiedEditorJs.includes('node("p", "editor-position-hint"'), "包含编辑实时联动提示栏");
  assert.ok(unifiedEditorJs.includes('node("div", "editor-preview"'), "包含实时排版预览容器");

  // 3. JS 逻辑：调用 QBRender / QB.renderQuestion、trackSource 与共享光标联动辅助模块
  assert.ok(unifiedEditorJs.includes("QB.renderQuestion"), "必须使用既有排版引擎 renderQuestion 渲染");
  assert.ok(unifiedEditorJs.includes("trackSource: true"), "必须启用 trackSource 追踪源码字符偏移");
  assert.ok(editorHelpersJs.includes("previewSelection"), "必须调用 previewSelection 同步竖杠光标与公式激活");
  assert.ok(unifiedEditorJs.includes("H.bindPreviewCursorSync"), "必须接入共享光标同步辅助函数");
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
