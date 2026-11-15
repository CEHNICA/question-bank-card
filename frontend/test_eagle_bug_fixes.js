import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const eagleHtmlPath = path.resolve(__dirname, 'eagle/index.html');

test('Eagle 7大核心Bug修复综合验证套件', async (t) => {
  const content = fs.readFileSync(eagleHtmlPath, 'utf-8');

  await t.test('Bug 1 & Bug 2: 题卡编辑分值与选项 E/F 完整性', () => {
    // 1. DOM 存在 E 和 F 选项输入框
    assert.ok(content.includes('id="editOptE"'), '应存在 editOptE 选项输入框');
    assert.ok(content.includes('id="editOptF"'), '应存在 editOptF 选项输入框');
    assert.ok(content.includes('id="editInputScore"'), '应存在 editInputScore 分值输入框');

    // 2. 光标联动字典完整注册
    assert.ok(content.includes("'editOptE': 'E'"), 'EDIT_INPUT_FIELDS 应注册 editOptE');
    assert.ok(content.includes("'editOptF': 'F'"), 'EDIT_INPUT_FIELDS 应注册 editOptF');
    assert.ok(content.includes("'E': '选项 E'"), 'EDIT_FIELD_LABELS 应注册 选项 E');
    assert.ok(content.includes("'F': '选项 F'"), 'EDIT_FIELD_LABELS 应注册 选项 F');

    // 3. openEditQuestionModal 赋初值
    assert.ok(content.includes("document.getElementById('editOptE').value = opts.E"), '应给 editOptE 赋初值');
    assert.ok(content.includes("document.getElementById('editOptF').value = opts.F"), '应给 editOptF 赋初值');
    assert.ok(content.includes("scoreInput.value = (q.score != null"), '应给 editInputScore 回填题目分值');

    // 4. submitQuestionEdit 保存分值与 E/F 选项
    assert.ok(content.includes("q.score = newScore"), '保存时应更新 q.score');
    assert.ok(content.includes("const e = document.getElementById('editOptE')?.value.trim();"), '保存时应读取 editOptE');
    assert.ok(content.includes("const f = document.getElementById('editOptF')?.value.trim();"), '保存时应读取 editOptF');
    assert.ok(content.includes("if (e) newOpts.E = e;"), '保存时应把 E 写入 newOpts');
    assert.ok(content.includes("if (f) newOpts.F = f;"), '保存时应把 F 写入 newOpts');
  });

  await t.test('Bug 3: 未入库题试题篮导出兜底转换', () => {
    assert.ok(content.includes('function convertQuestionDataToExportItem('), '应包含 convertQuestionDataToExportItem 转换方法');
    assert.ok(content.includes('localQ = questionsData.find('), 'loadExportItems 应在 missing 时从 questionsData 兜底');
    assert.ok(content.includes('items.push(convertQuestionDataToExportItem(localQ'), '兜底题目应成功转入导出 items');
  });

  await t.test('Bug 4 & Bug 7: Escape 快捷键联动与 isTyping 判空覆盖', () => {
    // Escape 联动
    assert.ok(content.includes("if (typeof closeSolutionEditor === 'function') closeSolutionEditor();"), 'Escape 应联动关闭答案解析编辑器');

    // isTyping 扩展
    assert.ok(content.includes('document.activeElement.isContentEditable'), 'isTyping 应检查 isContentEditable 防止富文本打字被拦截');
  });

  await t.test('Bug 5: 原卷扫描件 404 容错降级提示', () => {
    assert.ok(content.includes('img.onerror = () => {'), '原卷 img 应注册 onerror 事件处理');
    assert.ok(content.includes('opErrorNotice'), '应生成 opErrorNotice 友好提示卡片');
    assert.ok(content.includes('未找到高清原卷扫描件'), '应展示未找到高清原卷扫描件文案');
  });

  await t.test('Bug 6: 试题复制安全上下文 fallback 兜底', () => {
    assert.ok(content.includes('function copyTextToClipboard('), '应包含 copyTextToClipboard 统一复制方法');
    assert.ok(content.includes("document.execCommand('copy')"), '应包含 execCommand fallback');
    assert.ok(content.includes('copyTextToClipboard(textToCopy'), 'copyQuestionText 应调用 copyTextToClipboard');
  });

  await t.test('复制题目为图片能力验证 (copyQuestionImage)', () => {
    // 1. 函数定义与后端接口调用
    assert.ok(content.includes('async function copyQuestionImage(qid, button = null)'), '应包含 copyQuestionImage 函数');
    assert.ok(content.includes('/api/library/copy-image'), '应调用 /api/library/copy-image 后端出图接口');
    assert.ok(content.includes("mode: 'combined'"), '出图模式应为 combined 完整题面与配图');
    assert.ok(content.includes('正在生成…'), '出图期间按钮需显示正在生成提示');

    // 2. 界面挂载点：右键菜单、右栏属性面板、快速审题弹窗
    assert.ok(content.includes("copyQuestionImage('${q.id}')"), '右键菜单中应能触发 copyQuestionImage');
    assert.ok(content.includes('id="qlCopyImageBtn"'), '快速审题中应包含 qlCopyImageBtn 按钮');
    assert.ok(content.includes('id="metaCopyImageBtn"'), '右侧属性面板应包含 metaCopyImageBtn 按钮');

    // 3. 全局快捷键与说明
    assert.ok(content.includes("e.ctrlKey && e.shiftKey && (e.key === 'C' || e.key === 'c')"), '应支持 Ctrl+Shift+C 快捷键');
    assert.ok(content.includes('Ctrl + Shift + C'), '快捷键指南弹窗应声明 Ctrl + Shift + C');
  });

  await t.test('试题卡片右键菜单与操作栏优雅收纳 (openQuestionContextMenu)', () => {
    // 1. 试题专属右键菜单定义
    assert.ok(content.includes('function openQuestionContextMenu(e, qid)'), '应包含 openQuestionContextMenu 函数');
    assert.ok(content.includes('复制题目图片'), '右键菜单应包含复制题目图片选项');
    assert.ok(content.includes('核验证据 (原卷扫描件)'), '右键菜单应包含核验证据选项');
    assert.ok(content.includes('全屏快速审题'), '右键菜单应包含全屏快速审题选项');
    assert.ok(content.includes('编辑题目与选项'), '右键菜单应包含编辑题目选项');

    // 2. 卡片与紧凑行挂载 oncontextmenu 与 ··· 更多按钮
    assert.ok(content.includes("oncontextmenu=\"openQuestionContextMenu(event, '${q.id}')\""), '卡片与行节点应绑定 oncontextmenu');
    assert.ok(content.includes("openQuestionContextMenu(event, '${q.id}')"), '卡片更多按钮点击应触发 openQuestionContextMenu');
  });
});
