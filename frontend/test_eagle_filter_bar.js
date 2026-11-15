"use strict";
// Eagle 风格多维紧凑折叠胶囊筛选器测试套件
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const css = fs.readFileSync(path.join(__dirname, "eagle-filter-bar.css"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "eagle-filter-bar.js"), "utf8");


// ── 二、CSS 规范与视觉验证 ──
assert.match(css, /\.eagle-filter-container\s*\{/, "存在胶囊主容器样式");
assert.match(css, /\.eagle-capsule-btn\s*\{/, "存在胶囊按钮样式");
assert.match(css, /\.eagle-logic-segmented\s*\{/, "存在 AND/OR/NOT 分段控制器样式");
assert.match(css, /\.eagle-tag-panel\s*\{/, "存在专属标签面板样式");
assert.match(css, /\.eagle-dropdown-panel\.show\s*\{/, "面板展开时使用 show 类");
assert.doesNotMatch(css, /overflow-x:\s*auto\s*!important/, "胶囊容器严禁强制截断下拉");

// ── 三、JS 核心功能与逻辑验证 ──
assert.match(js, /window\.EagleFilterBar\s*=/, "导出了 EagleFilterBar 全局构造器");
assert.match(js, /window\.EagleFilterBarInstance\s*=/, "创建了全局单例 EagleFilterBarInstance");
assert.match(js, /qb-smart-folders-v2/, "定义了现代智能文件夹 Storage Key");
assert.match(js, /qb-library-saved-tag-filters/, "具备旧版常用标签组合平滑兼容与自动迁移");

// 验证包含全部/包含任一/排除模式
assert.match(js, /key:\s*"all",\s*label:\s*"包含全部 \(AND\)"/, "支持包含全部 AND 模式");
assert.match(js, /key:\s*"any",\s*label:\s*"包含任一 \(OR\)"/, "支持包含任一 OR 模式");
assert.match(js, /key:\s*"not",\s*label:\s*"排除这些 \(NOT\)"/, "支持排除这些 NOT 模式");

// 验证智能文件夹保存与恢复
assert.match(js, /function saveSmartFolder/, "具备智能文件夹保存逻辑");
assert.match(js, /function deleteSmartFolder/, "具备智能文件夹删除逻辑");
assert.match(js, /function getSmartFolders/, "具备智能文件夹读取与迁移逻辑");

console.log("Eagle filter bar static and architectural checks: OK");
