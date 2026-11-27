const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');

const eagleHtml = fs.readFileSync(path.join(__dirname, 'eagle', 'index.html'), 'utf8');
const unifiedEditorJs = fs.readFileSync(path.join(__dirname, 'library-unified-editor.js'), 'utf8');

describe('Eagle 工作台集成美化、原卷重框重识、防误触 Ctrl 快捷键与工作流契约', () => {
  it('录入按钮收进 ≡ 主菜单，顶栏与菜单不直接贴快捷键徽章，且保留内置偏好设置弹窗', () => {
    assert.doesNotMatch(eagleHtml, /id="btnToolbarIntake"/);
    const appMenuMatch = eagleHtml.match(/id="eagleAppMainMenu"[\s\S]*?<\/div>\s*\n\s*<!-- =/);
    assert.ok(appMenuMatch);
    assert.match(appMenuMatch[0], /href="\/"/);
    assert.doesNotMatch(appMenuMatch[0], /<kbd/);
    assert.match(eagleHtml, /id="eagleAppMainMenu"[\s\S]*?openEaglePreferencesModal\('general'\)/);
    assert.match(eagleHtml, /id="eaglePreferencesModal"[\s\S]*?data-pref-tab="general"[\s\S]*?data-pref-tab="display"[\s\S]*?data-pref-tab="transfer"[\s\S]*?data-pref-tab="help"[\s\S]*?data-pref-tab="about"/);
    assert.match(eagleHtml, /<script src="\/static\/api-settings\.js" defer><\/script>/);
    assert.match(eagleHtml, /<script src="\/static\/export-settings\.js" defer><\/script>/);
    assert.match(eagleHtml, /<script src="\/static\/library-transfer\.js" defer><\/script>/);
  });

  it('补全预编译 Tailwind v3 缺失的模糊/阴影工具类与右栏空闲概览卡', () => {
    assert.match(eagleHtml, /\.backdrop-blur-xs\s*\{/);
    assert.match(eagleHtml, /\.shadow-xs\s*\{/);
    assert.match(eagleHtml, /\.shadow-2xs\s*\{/);
    assert.match(eagleHtml, /id="inspectorIdleCard"/);
  });

  it('工作台快捷键全部采用简洁 Ctrl+键 组合防单键误触', () => {
    for (const k of ['i', 'k', 'f', 'o', 'u', 'm', 'e', 's', 'b', 'g']) {
      assert.match(eagleHtml, new RegExp(`lower === '${k}'`));
    }
    assert.match(eagleHtml, /key === ',' \|\| e\.code === 'Comma'/);
    assert.match(eagleHtml, /BracketLeft/);
    assert.match(eagleHtml, /BracketRight/);
    assert.match(eagleHtml, /e\.key >= '1' && e\.key <= '5'/);
    assert.doesNotMatch(eagleHtml, /if \(e\.key === 'e' \|\| e\.key === 'E'\)/);
    assert.doesNotMatch(eagleHtml, /if \(e\.key === 's' \|\| e\.key === 'S'\)/);
    assert.doesNotMatch(eagleHtml, /if \(e\.key === 'b' \|\| e\.key === 'B'\)/);
    assert.doesNotMatch(eagleHtml, /if \(e\.key === 't' \|\| e\.key === 'T'\)/);
  });

  it('原卷弹窗、统一编辑左栏、右键菜单与 QuickLook 接入原卷重框与重新 AI 识别闭环', () => {
    assert.match(eagleHtml, /id="btnOpRecropMode"/);
    assert.match(eagleHtml, /id="opCropOverlay"/);
    assert.match(eagleHtml, /id="opRecropPanel"/);
    assert.match(eagleHtml, /async function submitOpRecropOcrPreview\(\)/);
    assert.match(eagleHtml, /async function applyOpRecropOcr\(\)/);
    assert.match(eagleHtml, /\/api\/library\/\$\{encodeURIComponent\([^)]+\)\}\/recrop/);
    assert.match(eagleHtml, /id="qlRecropBtn"/);
    assert.match(eagleHtml, /openOriginalRecropModal\(/);
    assert.match(unifiedEditorJs, /openOriginalRecropModal/);
  });

  it('文件夹悬停即使未截断也展示完整层级路径（含母文件夹，如 例题）', () => {
    assert.match(eagleHtml, /function getFolderNodeFullPathParts\(nodeId\)/);
    assert.match(eagleHtml, /function getFolderNodeFullPath\(nodeId\)/);
    assert.match(eagleHtml, /const alwaysShowTip = anchor\.getAttribute\('data-tip-always'\) === '1';/);
    assert.match(eagleHtml, /data-tip-always="1"/);
  });

  it('OneNote 右栏精简为记笔记/打开/+/✕，移除第几刷与时间轴，保留图标与短标签平衡', () => {
    assert.doesNotMatch(eagleHtml, /data-menu-node="smart:onenote_timeline"/);
    assert.doesNotMatch(eagleHtml, /id:\s*'smart:onenote_timeline'/);
    assert.doesNotMatch(eagleHtml, /\+\s*第\$\{nextAttemptNo\}刷/);
    assert.match(eagleHtml, /打开'\s*:\s*'记笔记'/);
    assert.match(eagleHtml, /async function startOneNoteAttempt\(qid\)/);
    assert.match(eagleHtml, /\/api\/library\/\$\{qid\}\/onenote\/attempt/);
    assert.match(eagleHtml, /id="btnCopyInspectorLocator"[^>]*aria-label="复制题目定位码"[\s\S]*?<svg/);
    assert.doesNotMatch(eagleHtml, /id="btnCopyInspectorLocator"[\s\S]*?>📋 复制</);
    assert.match(eagleHtml, /id="btnInspectorEvidence"[\s\S]*?><span>📄<\/span><span>原卷<\/span><\/button>/);
    assert.match(eagleHtml, /const genLabel = isFresh \? '✨ 建议' : '重生成'/);
  });

  it('资源社区支持试卷/教材、普通文件夹、单题与多选上传及 OneNote 步骤附带，并移除题卡上方按钮', () => {
    assert.match(eagleHtml, /id="communityPublishModal"/);
    assert.match(eagleHtml, /id="communityImportModal"/);
    assert.match(eagleHtml, /id="commPubIncludeOneNote"/);
    assert.match(eagleHtml, /openCommunityPublishModal\('\$\{escapeInlineJsArg\(targetPaper\.id\)\}'\)/);
    assert.match(eagleHtml, /publishUserFolderToCommunity\('/);
    assert.match(eagleHtml, /publishQuestionToCommunity\('/);
    assert.match(eagleHtml, /batchPublishToCommunity\(\)/);
    assert.match(eagleHtml, /action === 'import_pack'/);
    assert.match(eagleHtml, /action === 'community_auth'/);
    assert.match(eagleHtml, /\/api\/backup\/fetch-and-inspect/);
    assert.match(eagleHtml, /\/api\/backup\/import-staged/);
    assert.match(eagleHtml, /loadFoldersFromServer\(\{ rerender: true \}\)/);
    assert.match(eagleHtml, /handleLocalZipDropImport\(zipFile\)/);
    const activeChipsMatch = eagleHtml.match(/function renderInlineActiveChips\(\)[\s\S]*?\n\s*if \(filterState\.keyword\)/);
    assert.ok(activeChipsMatch);
    assert.doesNotMatch(activeChipsMatch[0], /openCommunityPublishModal/);
  });
});
