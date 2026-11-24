"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const eagleHtmlPath = path.resolve(__dirname, "eagle/index.html");

test('Eagle 7大核心Bug修复综合验证套件', async (t) => {
  const content = fs.readFileSync(eagleHtmlPath, 'utf-8');

  const helpersJs = fs.readFileSync(path.join(__dirname, 'qb-editor-helpers.js'), 'utf8');
  const unifiedJs = fs.readFileSync(path.join(__dirname, 'library-unified-editor.js'), 'utf8');

  await t.test('Bug 1 & Bug 2: 题卡统一编辑保留分值与选项 A–H 完整性', () => {
    // 1. 统一工作台样式与共享脚本接入（含 A–H 动态选项与预览光标联动）
    assert.ok(content.includes('/question-editor.css'), 'Eagle 页应接入 /question-editor.css');
    assert.ok(content.includes('/qb-editor-helpers.js'), 'Eagle 页应接入 /qb-editor-helpers.js');
    assert.ok(content.includes('/library-unified-editor.js'), 'Eagle 页应接入 /library-unified-editor.js');
    assert.match(helpersJs, /const OPTION_KEYS\s*=\s*\["A",\s*"B",\s*"C",\s*"D",\s*"E",\s*"F",\s*"G",\s*"H"\]/, '共享助手应声明 A–H 选项键');

    // 2. 光标联动支持题干/答案/解析及全部动态选项输入
    assert.ok(helpersJs.includes('bindPreviewCursorSync'), '共享助手应提供 bindPreviewCursorSync');
    assert.ok(unifiedJs.includes('buildInputsMap'), '统一工作台应映射题干/答案/解析及动态选项输入框');
    assert.ok(unifiedJs.includes('`${field} 选项`'), '应为选项输入框生成「X 选项」预览联动标签');

    // 3. 统一保存同步与分值/选项持久化
    assert.ok(content.includes('function applyUnifiedSaveToEagle('), '应定义 applyUnifiedSaveToEagle 同步题卡状态');
    assert.ok(content.includes('function buildEagleCardContentHtml('), '应定义 buildEagleCardContentHtml 渲染卡片 A–H 选项');
    assert.ok(content.includes("item?.score"), '统一保存同步应支持后端返回的题目分值同步');
    assert.ok(content.includes("['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'].includes(slot)"), '卡片选项与配图渲染应完整支持 A–H');
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

  await t.test('标签浮层搜索必须按输入收列表', () => {
    const start = content.indexOf('function renderTagFilterDropdownList()');
    assert.ok(start > 0, '必须定义 renderTagFilterDropdownList()');
    const slice = content.slice(start, start + 2200);
    assert.match(slice, /dropTagSearchInput/, '重画标签列表时必须读取搜索框');
    assert.match(slice, /matchTagByQuery/, '必须走和联想框同一套名字/别名/拼音匹配');
    assert.match(slice, /没有匹配/, '一个都对不上时要有空态，不能继续铺全表');
    assert.match(content, /id="dropTagSearchInput"[\s\S]{0,180}oninput="renderTagFilterDropdownList\(\)"/);
  });

  await t.test('筛选条一个维度只有一个值时不画组头', () => {
    const start = content.indexOf('function renderInlineActiveChips()');
    assert.ok(start > 0, '必须定义 renderInlineActiveChips()');
    const end = content.indexOf('\n    function removeFilterChip(', start);
    const slice = content.slice(start, end > start ? end : start + 9000);
    assert.match(slice, /tagChips\.length > 1[\s\S]{0,160}renderFilterGroupHead\('标签·'/, '标签只在多值时画「标签·全含/任一」组头');
    assert.match(slice, /typeChips\.length > 1[\s\S]{0,80}renderFilterGroupHead\('题型'/, '题型只在多值时画组头');
    assert.match(slice, /starChips\.length > 1[\s\S]{0,80}renderFilterGroupHead\('评分'/, '评分只在多值时画组头');
    assert.match(slice, /statusChips\.length > 1[\s\S]{0,80}renderFilterGroupHead\('完整度与笔记'/, '完整度只在多值时画组头');
    assert.doesNotMatch(
      slice,
      /if \(tagChips\.length\) \{\s*const logicLabel[\s\S]{0,80}renderFilterGroupHead/,
      '不得无条件地给单值标签再画一颗组头'
    );
  });

  await t.test('顶栏关键词搜索覆盖右侧详情字段', () => {
    const start = content.indexOf('function questionMatchesKeyword(');
    assert.ok(start > 0, '必须抽出 questionMatchesKeyword()');
    const slice = content.slice(start, start + 2800);
    assert.match(slice, /getQuestionAnnotation/, '必须搜自定义名称/注释/链接');
    assert.match(slice, /q\.tags/, '必须搜本题已贴标签');
    assert.match(slice, /matchTagByQuery/, '标签要命中别名/拼音');
    assert.match(slice, /readUserFolders/, '必须搜所属自定义文件夹名');
    assert.match(slice, /isQuestionInDocFolder/, '必须搜所属试卷/教材名');
    assert.match(slice, /onenote/, '必须搜 OneNote 页标题');
    assert.match(
      content,
      /if \(s\.keyword && !questionMatchesKeyword\(q, s\.keyword\)\) return false;/,
      'questionMatchesFilterSpec 必须走 questionMatchesKeyword'
    );
    assert.match(
      content,
      /placeholder="搜索题干、名称、标签、文件夹…/,
      '占位符要写明能搜详情字段'
    );
  });

  await t.test('筛选条项目与文件夹不得双写同一份卷子', () => {
    assert.match(content, /function folderCoversPaperChip\(/, '必须有 folderCoversPaperChip()');
    assert.match(content, /function clearActiveFolderScope\(/, '关掉文件夹时要清被覆盖的项目条件');
    const start = content.indexOf('function renderInlineActiveChips()');
    const end = content.indexOf('\n    function removeFilterChip(', start);
    const slice = content.slice(start, end > start ? end : start + 4000);
    assert.match(slice, /folderCoversPaperChip\(folderNode\)/, '画项目胶囊前必须检查文件夹是否已覆盖');
    assert.match(slice, /paperId && !paperCoveredByFolder/, '被覆盖时不得再画项目胶囊');
    assert.match(slice, /clearActiveFolderScope\(\)/, '文件夹胶囊的 × 必须走 clearActiveFolderScope');
  });

  await t.test('复制题目为图片能力验证 (copyQuestionImage)', () => {
    // 1. 函数定义与后端接口调用
    assert.ok(content.includes('async function copyQuestionImage(qid, button = null)'), '应包含 copyQuestionImage 函数');
    assert.ok(content.includes('/api/library/copy-image'), '应调用 /api/library/copy-image 后端出图接口');
    assert.ok(content.includes("mode: 'combined'"), '出图模式应为 combined 完整题面与配图');
    assert.ok(content.includes('正在生成…'), '出图期间按钮需显示正在生成提示');

    // 2. 界面挂载点：右键菜单、快速审题弹窗（右侧属性面板已不再重复放复制图片）
    assert.ok(content.includes("copyQuestionImage('${q.id}')"), '右键菜单中应能触发 copyQuestionImage');
    assert.ok(content.includes('id="qlCopyImageBtn"'), '快速审题中应包含 qlCopyImageBtn 按钮');

    // 3. 全局快捷键与说明
    assert.ok(content.includes("e.ctrlKey && e.shiftKey && (e.key === 'C' || e.key === 'c')"), '应支持 Ctrl+Shift+C 快捷键');
    assert.ok(content.includes('Ctrl + Shift + C'), '快捷键指南弹窗应声明 Ctrl + Shift + C');
  });

  await t.test('试题卡片右键菜单与操作栏优雅收纳 (openQuestionContextMenu)', () => {
    // 1. 试题专属右键菜单定义：已整合为单一「编辑」入口
    assert.ok(content.includes('function openQuestionContextMenu(e, qid)'), '应包含 openQuestionContextMenu 函数');
    assert.ok(content.includes('复制题目图片'), '右键菜单应包含复制题目图片选项');
    assert.ok(content.includes('全屏快速审题'), '右键菜单应包含全屏快速审题选项');
    assert.ok(content.includes("openSolutionEditorForId('${q.id}', { focusField: 'stem' })"), '右键菜单应统一调用 openSolutionEditorForId 定位到题干编辑');
    assert.ok(!content.includes('修改与校订题目'), '右键菜单不应保留分裂的「修改与校订题目」旧入口');
    assert.ok(!content.includes('编辑答案与解析'), '右键菜单不应保留分裂的「编辑答案与解析」旧入口');
    // 核验证据在题卡按钮上，右键不再重复
    assert.ok(content.includes('核验证据'), '题卡应保留核验证据入口');
    assert.ok(!content.includes('核验证据 (原卷扫描件)'), '右键不应再重复核验证据');

    // 2. 卡片与紧凑行挂载 oncontextmenu 与 ··· 更多按钮
    assert.ok(content.includes("oncontextmenu=\"openQuestionContextMenu(event, '${q.id}')\""), '卡片与行节点应绑定 oncontextmenu');
    assert.ok(content.includes("openQuestionContextMenu(event, '${q.id}')"), '卡片更多按钮点击应触发 openQuestionContextMenu');
  });

  await t.test('Eagle 统一编辑工作台接入与题干版本迁移持久化', () => {
    assert.ok(content.includes('eagle-solution-editor.js'), 'Eagle 页必须加载 /eagle-solution-editor.js');
    assert.ok(content.includes('library-unified-editor.js'), 'Eagle 页必须加载 /library-unified-editor.js');
    assert.ok(content.includes('function openSolutionEditorFromQuickLook('), '应定义 openSolutionEditorFromQuickLook()');
    assert.ok(content.includes('function openEditFromQuickLook('), '应定义 openEditFromQuickLook()');
    assert.ok(content.includes('function openSolutionEditorForId('), '应定义 openSolutionEditorForId()');
    assert.ok(content.includes('window.renderQuickLook ='), '应暴露 window.renderQuickLook 供保存后联动刷新');
    assert.ok(content.includes("'quickLookQuestionId'"), '应暴露 window.quickLookQuestionId');
    assert.ok(content.includes("onclick=\"openSolutionEditorFromQuickLook()\""), 'QuickLook 解析区按钮应接入统一编辑工作台并聚焦答案解析');
    assert.ok(content.includes("openSolutionEditorForId('${q.id}')"), '题卡解析编辑入口应保留');

    const saveStart = content.indexOf('function applyUnifiedSaveToEagle(');
    const saveEnd = content.indexOf('function openEditQuestionModal(', saveStart);
    assert.ok(saveStart > 0 && saveEnd > saveStart, '应定义 applyUnifiedSaveToEagle 与转发兼容入口');
    const saveSlice = content.slice(saveStart, saveEnd);
    assert.ok(saveSlice.includes('migrateQuestionIdAfterEdit'), '题干改版生成新 UUID 后应迁移前端题卡 ID');
    assert.ok(saveSlice.includes('buildEagleCardContentHtml'), '保存同步时应重建前端题卡排版正文');

    assert.ok(unifiedJs.includes('nextBaseRevision = null'), '题干发布新版本后再保存解析应清空旧版 base_revision 触发安全资产迁移');
    assert.ok(unifiedJs.includes('base_revision: nextBaseRevision ?? null'), '保存正式解析应携带 base_revision');
    assert.ok(!unifiedJs.includes('close();\n      saveCurrent('), '后端保存失败时不得关闭工作台丢弃用户输入');
  });

  await t.test('打开已关联 OneNote 笔记期间必须显示等待条直到唤醒完成', () => {
    const openStart = content.indexOf('async function openOneNotePage(');
    const createStart = content.indexOf('async function createOneNotePage(');
    assert.ok(openStart > 0 && createStart > openStart);
    const openSlice = content.slice(openStart, createStart);
    assert.ok(openSlice.includes('showOneNoteBusy(qid, ONE_NOTE_OPEN_STEPS)'), 'openOneNotePage 应显示顶部等待条与按钮忙态');
    assert.ok(openSlice.includes('/onenote/open?wait=1'), 'openOneNotePage 应同步等待唤醒结束再关闭等待条');
    assert.ok(openSlice.includes('hideOneNoteBusy(token)'), 'openOneNotePage 必须在 finally 中收起等待条');
  });
});
