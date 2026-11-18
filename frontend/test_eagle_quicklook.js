const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const INDEX_HTML_PATH = path.join(__dirname, 'eagle', 'index.html');

test('quickLookModal 保留所有既有 ID 并采用单栏极简布局与不折行控制', () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');

  const start = html.indexOf('id="quickLookModal"');
  assert.ok(start > -1, '应包含 quickLookModal 弹窗');
  const end = html.indexOf('id="originalPaperModal"', start);
  assert.ok(end > start, '应找到 quickLookModal 结束边界');
  const section = html.slice(start, end);

  const requiredIds = [
    'qlTitleBadge',
    'qlNameInput',
    'qlTypeBadge',
    'qlStarBadge',
    'qlScoreBadge',
    'qlSolutionStatusBadge',
    'qlBasketBtn',
    'qlFavoriteBtn',
    'qlCopyImageBtn',
    'qlReadingPane',
    'qlContentContainer',
    'qlAnalysisContainer',
    'qlPaperBtn',
    'qlTagsContainer',
    'qlSourceText',
  ];
  for (const id of requiredIds) {
    assert.ok(section.includes(`id="${id}"`), `quickLookModal 应保留节点 #${id}`);
  }

  assert.ok(section.includes('max-w-[920px]'), 'quickLookModal 应采用适中的 920px 单栏阅读宽度');
  assert.ok(!section.includes('<aside'), '精简后应移除重复的右侧信息侧栏');
  assert.ok(section.includes('whitespace-nowrap shrink-0'), '顶栏与底栏按钮、徽标应禁止折行');

  // 灯箱层级必须高于 quickLookModal，避免审题内点击放大配图被遮挡
  assert.ok(
    html.includes('id="imageLightboxModal"') && html.includes('z-[10010]'),
    'imageLightboxModal 应具备高于 quickLookModal(10005) 的 z-index'
  );
});

test('quickLook 脚本仅渲染已确认标签且保留完整结构化排版与动作能力', () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  const scriptMatches = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
  assert.ok(scriptMatches.length > 0, 'eagle/index.html 应包含内联脚本');
  const mainScript = scriptMatches[scriptMatches.length - 1][1];

  // 验证脚本语法可被 JS 引擎无错编译
  assert.doesNotThrow(() => new vm.Script(mainScript), 'eagle/index.html 主内联脚本应无语法错误');

  const requiredFns = [
    'function openQuickLookModal',
    'function closeQuickLookModal',
    'function navQuickLook',
    'function renderQuickLookModal',
    'function renderQuickLookStem',
    'function renderQuickLookSolution',
    'function renderQuickLookMeta',
    'function openEditFromQuickLook',
    'function openPaperFromQuickLook',
    'function triggerQuickLookAiAnalysis',
  ];
  for (const fn of requiredFns) {
    assert.ok(mainScript.includes(fn), `内联脚本应定义 ${fn}`);
  }

  const metaFnStart = mainScript.indexOf('function renderQuickLookMeta');
  const metaFnEnd = mainScript.indexOf('function renderQuickLookModal', metaFnStart);
  assert.ok(metaFnStart > -1 && metaFnEnd > metaFnStart, '应找到 renderQuickLookMeta 函数体');
  const metaFnBody = mainScript.slice(metaFnStart, metaFnEnd);
  assert.ok(metaFnBody.includes('!t.needsReview'), '快速审题只应展示已确认标签，过滤 needsReview');
  assert.ok(!metaFnBody.includes('q.aiSuggestedTags'), '快速审题不应读取未采纳的 q.aiSuggestedTags');

  assert.ok(mainScript.includes('function toggleQuickLookAnalysis'), '应定义 toggleQuickLookAnalysis 切换答案显示');
  assert.ok(mainScript.includes('let quickLookShowAnalysis = false'), '快速审题默认应隐藏答案与解析');
  assert.ok(mainScript.includes('qlToggleAnalysisBtn'), '应提供显式展开/收起答案按钮');
  assert.ok(mainScript.includes('savedSolution'), 'renderQuickLookSolution 应优先展示正式解析 savedSolution');
  assert.ok(mainScript.includes('qlAiStatusHost_'), 'AI 推导应支持在快速审题弹窗内直接承载进度状态');
});
