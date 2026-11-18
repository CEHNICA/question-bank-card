"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const eagleHtmlPath = path.resolve(__dirname, "eagle/index.html");

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
    // 1. 试题专属右键菜单定义：只保留卡片上没有的动作
    assert.ok(content.includes('function openQuestionContextMenu(e, qid)'), '应包含 openQuestionContextMenu 函数');
    assert.ok(content.includes('复制题目图片'), '右键菜单应包含复制题目图片选项');
    assert.ok(content.includes('全屏快速审题'), '右键菜单应包含全屏快速审题选项');
    assert.ok(content.includes('修改与校订题目'), '右键菜单应包含编辑题目选项');
    // 核验证据在题卡按钮上，右键不再重复
    assert.ok(content.includes('核验证据'), '题卡应保留核验证据入口');
    assert.ok(!content.includes('核验证据 (原卷扫描件)'), '右键不应再重复核验证据');

    // 2. 卡片与紧凑行挂载 oncontextmenu 与 ··· 更多按钮
    assert.ok(content.includes("oncontextmenu=\"openQuestionContextMenu(event, '${q.id}')\""), '卡片与行节点应绑定 oncontextmenu');
    assert.ok(content.includes("openQuestionContextMenu(event, '${q.id}')"), '卡片更多按钮点击应触发 openQuestionContextMenu');
  });

  await t.test('Eagle 统一答案编辑器接入与题干版本迁移持久化', () => {
    assert.ok(content.includes('<script src="/eagle-solution-editor.js"'), 'Eagle 页必须加载 /eagle-solution-editor.js');
    assert.ok(content.includes('function openSolutionEditorFromQuickLook('), '应定义 openSolutionEditorFromQuickLook()');
    assert.ok(content.includes('function openSolutionEditorForId('), '应定义 openSolutionEditorForId()');
    assert.ok(content.includes('window.renderQuickLook ='), '应暴露 window.renderQuickLook 供答案保存后联动刷新');
    assert.ok(content.includes("'quickLookQuestionId'"), '应暴露 window.quickLookQuestionId');
    assert.ok(content.includes("onclick=\"openSolutionEditorFromQuickLook()\""), 'QuickLook 解析区按钮应接入统一答案编辑器');
    assert.ok(content.includes("openSolutionEditorForId('${q.id}')"), '题卡与右键菜单应提供答案编辑器入口');

    const editStart = content.indexOf('async function openEditQuestionModal(');
    const submitStart = content.indexOf('async function submitQuestionEdit(');
    const submitEnd = content.indexOf('// ================= 快捷键与弹窗通用逻辑', submitStart);
    assert.ok(editStart > 0 && submitStart > editStart && submitEnd > submitStart);
    const editSlice = content.slice(editStart, submitStart);
    const submitSlice = content.slice(submitStart, submitEnd);
    assert.ok(editSlice.includes('q.savedSolution'), 'openEditQuestionModal 应优先回填已保存的正式解析');
    assert.ok(editSlice.includes('activeEditingRevisionPromise'), 'openEditQuestionModal 应跟踪修订信息请求避免竞态');
    assert.ok(submitSlice.includes('migrateQuestionIdAfterEdit'), '题干改版生成新 UUID 后应迁移前端题卡 ID');
    assert.ok(submitSlice.includes('base_revision: baseRevision'), '保存正式解析应携带 base_revision');
    assert.ok(!submitSlice.includes('closeEditQuestionModal();\n      if (savedOk)'), '后端保存失败时不得关闭编辑弹窗丢弃用户输入');
  });
});
