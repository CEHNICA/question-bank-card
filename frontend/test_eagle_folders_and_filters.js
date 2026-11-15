// @ts-check
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const eagleHtmlPath = path.join(__dirname, 'eagle', 'index.html');
const eagleHtml = fs.readFileSync(eagleHtmlPath, 'utf8');

/**
 * 构建轻量 DOM + localStorage 沙盒以运行 eagle/index.html 内联脚本中的纯逻辑与渲染方法
 */
function createEagleRuntimeSandbox(options = {}) {
  const storageMap = new Map(Object.entries(options.localStorage || {}));
  const elementsById = new Map();

  const makeMockEl = (id) => {
    const classSet = new Set();
    const el = {
      id,
      innerText: '',
      textContent: '',
      innerHTML: '',
      value: '',
      className: '',
      children: [],
      style: {},
      classList: {
        add: (...cls) => cls.forEach((c) => classSet.add(c)),
        remove: (...cls) => cls.forEach((c) => classSet.delete(c)),
        contains: (c) => classSet.has(c),
        toggle: (c) => (classSet.has(c) ? classSet.delete(c) : classSet.add(c))
      },
      appendChild: (c) => {
        el.children.push(c);
        return c;
      },
      insertBefore: (c) => {
        el.children.unshift(c);
        return c;
      },
      querySelectorAll: () => [],
      addEventListener: () => {},
      setAttribute: () => {},
      getAttribute: () => ''
    };
    return el;
  };

  const trackedIds = [
    'folderTreeContainer',
    'treeSortSelect',
    'inlineActiveChipsContainer',
    'btnClearAllFilters',
    'pillLabelTag',
    'pillBadgeTag',
    'pillBadgeType',
    'pillBadgeStar',
    'pillBadgeStatus',
    'btnRequireAnswer',
    'btnRequireAnalysis',
    'btnRequireOneNote',
    'matchedQuestionCount',
    'questionListContainer',
    'globalSearchInput',
    'folderPickerModal',
    'folderPickerTitle',
    'folderPickerSubtitle',
    'folderPickerTreeList',
    'folderPickerNewBtn',
    'batchActionBar',
    'batchCountBadge',
    'selectedCountText',
    'inspectorContent',
    'inspectorEmpty',
    'toastContainer',
    'recycleBinPanel',
    'recycleBinTotalBadge',
    'recycleBinFilterTabs',
    'recycleBinListContainer',
    'deleteConfirmModal',
    'deleteConfirmHeader',
    'deleteConfirmIcon',
    'deleteConfirmTitle',
    'deleteConfirmSummary',
    'deleteConfirmImpactList',
    'deleteConfirmBtn',
    'inspectorTitle',
    'inspectorBadge',
    'questionMetaCard',
    'inspectorEagleTopMeta',
    'inspectorNameInput',
    'inspectorNoteInput',
    'inspectorUrlInput',
    'inspectorUrlActions',
    'inspectorFoldersContainer',
    'metaOneNoteBox',
    'qlNameInput',
    'leftFolderSidebar',
    'btnReopenLeftSidebar',
    'btnCollapsedAppMenu',
    'eagleAppMainMenu',
    'menuBadgeTagCount',
    'menuBadgeOneNote',
    'menuBadgeUncategorized',
    'menuBadgeNeedTags',
    'menuBtnAiUnverified',
    'menuBadgeAiUnverified',
    'menuBadgeTrash',
    'dropTagListContainer',
    'dropTagSearchInput'
  ];
  trackedIds.forEach((id) => elementsById.set(id, makeMockEl(id)));

  let promptReply = options.promptReply || '';
  let confirmReply = options.confirmReply !== undefined ? options.confirmReply : true;
  const toasts = [];

  const mockDocument = {
    getElementById: (id) => elementsById.get(id) || null,
    querySelectorAll: () => [],
    createElement: (tag) => makeMockEl(`__created_${tag}`),
    createDocumentFragment: () => ({ children: [], appendChild: (c) => c }),
    addEventListener: () => {},
    body: makeMockEl('body')
  };

  const localStorage = {
    getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
    setItem: (k, v) => {
      storageMap.set(String(k), String(v));
    },
    removeItem: (k) => {
      storageMap.delete(String(k));
    }
  };

  const sandbox = {
    console,
    Math,
    Date,
    Set,
    Map,
    Array,
    Object,
    JSON,
    String,
    Number,
    Boolean,
    RegExp,
    Error,
    parseInt,
    parseFloat,
    isNaN,
    encodeURIComponent,
    decodeURIComponent,
    setTimeout,
    clearTimeout,
    document: mockDocument,
    localStorage,
    IntersectionObserver: function () {
      return { observe: () => {}, unobserve: () => {}, disconnect: () => {} };
    },
    fetch: async () => ({ ok: true, json: async () => ({ ok: true }) }),
    URL: { createObjectURL: () => '', revokeObjectURL: () => {} },
    Blob: function () {}
  };

  const mockWindow = {
    location: { href: 'http://127.0.0.1:8769/eagle', origin: 'http://127.0.0.1:8769', search: '' },
    prompt: () => promptReply,
    confirm: () => confirmReply,
    addEventListener: () => {},
    localStorage,
    document: mockDocument
  };
  sandbox.window = mockWindow;
  sandbox.globalThis = sandbox;

  const scripts = [...eagleHtml.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/gi)];
  assert.ok(scripts.length > 0, 'eagle/index.html 必须包含内联脚本');
  vm.createContext(sandbox);
  for (const m of scripts) {
    vm.runInContext(m[1], sandbox);
  }

  // 测试辅助控制器
  vm.runInContext(
    `
    function __setTestData(papers, questions, favIds) {
      papersData.splice(0, papersData.length, ...(papers || []));
      questionsData.splice(0, questionsData.length, ...(questions || []));
      favoritesSet.clear();
      (favIds || []).forEach(id => favoritesSet.add(id));
    }
    function __getFilterState() { return filterState; }
    function __getFilteredIds() { return JSON.stringify(filteredQuestions.map(q => q.id)); }
    function __getQuestionsLength() { return questionsData.length; }
    function __getActiveNodeId() { return folderTreeState.activeId; }
    function __hasExpandedNode(id) { return folderTreeState.expanded.has(id); }
    `,
    sandbox
  );

  // 替换 showToast 以便测试断言，同时保留原始行为接口
  sandbox.showToast = (msg, type) => {
    toasts.push({ msg, type });
  };

  return {
    sandbox,
    storageMap,
    elementsById,
    toasts,
    setPromptReply: (v) => {
      promptReply = v;
    },
    setConfirmReply: (v) => {
      confirmReply = v;
    }
  };
}

test('Eagle OneNote 筛选、动态智能文件夹计数与多级子文件夹回归测试', async (t) => {
  await t.test('1. HTML 结构包含状态栏 OneNote 筛选项、新建文件夹入口、选择器弹窗、删除提醒弹窗与回收站面板', () => {
    assert.match(eagleHtml, /id="btnRequireOneNote"/, '状态筛选下拉框必须包含 #btnRequireOneNote');
    assert.match(eagleHtml, /onclick="toggleRequireOneNote\(\)"/, '状态筛选按钮必须绑定 toggleRequireOneNote()');
    assert.match(eagleHtml, /onclick="createUserFolder\(null\)"/, '左侧边栏标题栏必须包含新建文件夹按钮');
    assert.match(eagleHtml, /id="folderPickerModal"/, '必须定义文件夹选择器弹窗 #folderPickerModal');
    assert.match(eagleHtml, /id="deleteConfirmModal"/, '必须定义删除提醒弹窗 #deleteConfirmModal');
    assert.match(eagleHtml, /id="recycleBinPanel"/, '必须定义主视图回收站面板 #recycleBinPanel');
    assert.match(eagleHtml, /onclick="batchAssignToFolder\(\)"/, '批量操作栏必须包含放入文件夹按钮');
  });

  await t.test('2. OneNote 筛选与 chips、重置、快捷视图联动准确', async () => {
    const { sandbox, elementsById } = createEagleRuntimeSandbox();
    const papers = [{ id: 'p1', name: '1011胜利初三数学月考.pdf', published: 3, materialType: 'exam', createdAt: new Date() }];
    const questions = [
      {
        id: 'q1',
        documentId: 'p1',
        questionNumber: 1,
        questionTypeCode: 'single_choice',
        difficultyStars: 4,
        hasAnswer: true,
        hasAnalysis: true,
        tags: [{ id: 't1', name: '二次函数', color: 'emerald' }],
        rawStem: '抛物线顶点坐标',
        content: '抛物线顶点坐标',
        source: '1011胜利初三数学月考.pdf',
        onenote: { url: 'onenote:///page-1', pageId: '{111}', pageTitle: '第1题' }
      },
      {
        id: 'q2',
        documentId: 'p1',
        questionNumber: 2,
        questionTypeCode: 'solution',
        difficultyStars: 5,
        hasAnswer: true,
        hasAnalysis: false,
        tags: [],
        rawStem: '相似三角形证明',
        content: '相似三角形证明',
        source: '1011胜利初三数学月考.pdf',
        onenote: { url: '' }
      },
      {
        id: 'q3',
        documentId: 'p1',
        questionNumber: 3,
        questionTypeCode: 'fill_in',
        difficultyStars: 3,
        hasAnswer: false,
        hasAnalysis: false,
        tags: [],
        rawStem: '一次函数斜率',
        content: '一次函数斜率',
        source: '1011胜利初三数学月考.pdf'
      }
    ];
    sandbox.__setTestData(papers, questions, []);

    assert.equal(sandbox.hasQuestionOneNote(questions[0]), true);
    assert.equal(sandbox.hasQuestionOneNote(questions[1]), false);
    assert.equal(sandbox.hasQuestionOneNote(questions[2]), false);

    // 通过顶部状态下拉按钮切换 OneNote 筛选
    sandbox.toggleRequireOneNote();
    assert.equal(sandbox.__getFilterState().hasOneNote, true);
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1']);
    assert.match(elementsById.get('inlineActiveChipsContainer').innerHTML, /OneNote有笔记/);
    assert.equal(elementsById.get('pillBadgeStatus').textContent, '1');

    // 点击 chip 关闭 OneNote 条件
    sandbox.removeFilterChip('onenote', '');
    assert.equal(sandbox.__getFilterState().hasOneNote, false);
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1', 'q2', 'q3']);

    // 通过收纳至左上角 ≡ 菜单的 smart:onenote 视图筛选，且解绑/新建后菜单计数实时变化
    sandbox.renderFolderTree();
    assert.equal(elementsById.get('menuBadgeOneNote').innerText, '1');

    sandbox.applyFolderNode('smart:onenote');
    assert.equal(sandbox.__getFilterState().smartFilter, 'onenote');
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1']);

    // 模拟为 q3 新增 OneNote 笔记并刷新
    questions[2].onenote = { pageTitle: '第3题笔记' };
    sandbox.refreshOneNoteFilterAndCounts();
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1', 'q3']);
    assert.equal(elementsById.get('menuBadgeOneNote').innerText, '2');

    // 模拟重设筛选条件
    sandbox.resetAllFilters();
    assert.equal(sandbox.__getFilterState().hasOneNote, false);
    assert.equal(sandbox.__getFilterState().smartFilter, '');
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1', 'q2', 'q3']);

    // OneNote 功能已精简：只保留「进入/打开 OneNote」与「取消链接」两个动作
    assert.doesNotMatch(
      eagleHtml,
      /function bindOneNotePage|function saveInspectorOneNoteUrl|function copyOneNoteLink|function parseOneNoteInputPayload/,
      'OneNote 冗余函数（手动绑定/更换/复制/解析）必须删除'
    );
    assert.doesNotMatch(eagleHtml, /inspectorOneNoteUrlInput/, '不得再保留 OneNote 链接输入框');
    assert.doesNotMatch(eagleHtml, /oneNoteUnbindModal/, '解除关联改用 window.confirm，不得保留自定义弹窗');

    // 聚焦 q1 后，右侧详情栏只剩「打开笔记」与「取消关联」两个动作
    sandbox.focusQuestion('q1');
    const oneNoteBoxHtml = elementsById.get('metaOneNoteBox').innerHTML;
    assert.match(eagleHtml, /id="onenoteBusyBar"/, 'OneNote 操作期间要有持续可见的提示条');
    assert.match(eagleHtml, /function showOneNoteBusy/, '提示条要停到本次操作结束');
    assert.match(oneNoteBoxHtml, /goOneNote/, '必须提供 goOneNote 统一入口');
    assert.match(oneNoteBoxHtml, /unbindOneNotePage/, '必须保留取消关联入口');
    assert.doesNotMatch(oneNoteBoxHtml, /saveInspectorOneNoteUrl|copyOneNoteLink/, '不得再出现更换/复制入口');

    // 统一入口函数存在：已关联时走打开，未关联时走创建
    assert.equal(typeof sandbox.goOneNote, 'function');
    assert.equal(typeof sandbox.openOneNotePage, 'function');
    assert.equal(typeof sandbox.createOneNotePage, 'function');
  });

  await t.test('3. 修复自定义智能文件夹计数恒为 0 的缺陷，并支持旧数据、嵌套与删除入回收站', async () => {
    const legacyCustomFolders = [
      {
        id: 'csf_legacy_paper',
        name: '1011胜利初三数学月考.pdf',
        filter: {
          paperIds: ['p1'],
          smartFilter: '',
          questionTypes: [],
          difficultyStars: [],
          hasAnswer: false,
          hasAnalysis: false,
          selectedTagIds: []
        }
      },
      {
        id: 'csf_child_onenote',
        name: '其中有 OneNote 笔记',
        parentId: 'csf_legacy_paper',
        filter: {
          paperIds: ['p1'],
          hasOneNote: true
        }
      }
    ];
    const { sandbox, elementsById } = createEagleRuntimeSandbox({
      localStorage: {
        eagle_custom_smart_folders: JSON.stringify(legacyCustomFolders)
      }
    });
    const papers = [
      { id: 'p1', name: '1011胜利初三数学月考.pdf', published: 2, materialType: 'exam', createdAt: new Date() },
      { id: 'p2', name: '另一份试卷.pdf', published: 1, materialType: 'exam', createdAt: new Date() }
    ];
    const questions = [
      { id: 'q1', documentId: 'p1', questionNumber: 1, tags: [], onenote: { url: 'onenote:///1' } },
      { id: 'q2', documentId: 'p1', questionNumber: 2, tags: [] },
      { id: 'q3', documentId: 'p2', questionNumber: 1, tags: [], onenote: { url: 'onenote:///3' } }
    ];
    sandbox.__setTestData(papers, questions, []);

    const roots = sandbox.getFolderTreeRoots();
    const customSection = roots.find((r) => r.id === 'section:custom');
    assert.ok(customSection, '左侧树必须包含智能文件夹分段 section:custom');
    assert.equal(customSection.children.length, 1);

    const parentSmart = customSection.children[0];
    assert.equal(parentSmart.label, '1011胜利初三数学月考.pdf');
    assert.equal(parentSmart.count, 2, '旧版智能文件夹必须实时算出匹配题数 2，而不是 0');
    assert.equal(parentSmart.children.length, 1);
    assert.equal(parentSmart.children[0].label, '其中有 OneNote 笔记');
    assert.equal(parentSmart.children[0].count, 1, '子智能文件夹必须实时算出匹配题数 1');

    // 防成环：不能把父智能文件夹移到自己或子智能文件夹下
    assert.equal(sandbox.moveCustomSmartFolder('csf_legacy_paper', 'csf_child_onenote'), false);

    // 删除智能文件夹应先弹出确认提醒弹窗，确认后递归移入回收站并切换视图
    sandbox.renderFolderTree();
    sandbox.applyFolderNode('custom:csf_child_onenote');
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1']);
    sandbox.deleteCustomSmartFolder('csf_legacy_paper');
    assert.equal(elementsById.get('deleteConfirmModal').classList.contains('hidden'), false, '删除智能文件夹必须先显示确认弹窗');
    assert.equal(sandbox.readCustomSmartFolders().length, 2, '用户未确认前绝不能提前删除');

    await sandbox.executePendingDelete();
    assert.equal(sandbox.readCustomSmartFolders().length, 0);
    assert.equal(sandbox.readLocalTrash().smartFolders.length, 1, '已删除的智能文件夹及其子项应进入本地回收站');
    assert.equal(sandbox.__getActiveNodeId(), 'all', '删除当前激活智能文件夹后应自动回退到全部题库');
  });

  await t.test('4. 统一文件夹树支持多级嵌套、试卷/教材建子文件夹、防成环移动与子树去重汇总', () => {
    const harness = createEagleRuntimeSandbox();
    const { sandbox } = harness;
    const questions = [
      { id: 'q1', documentId: 'p1', questionNumber: 1, tags: [] },
      { id: 'q2', documentId: 'p1', questionNumber: 2, tags: [] },
      { id: 'q3', documentId: 'b1', questionNumber: 1, section: '第1章 第一节', paperPage: 1, tags: [] }
    ];
    sandbox.__setTestData(
      [
        { id: 'p1', name: '卷A', published: 2, materialType: 'exam', createdAt: new Date() },
        { id: 'b1', name: '人教A版必修第一册', published: 1, materialType: 'book', createdAt: new Date() }
      ],
      questions,
      []
    );

    // 创建两层文件夹：高三错题 -> 几何专题 -> 折叠最值
    harness.setPromptReply('高三错题');
    const rootFolder = sandbox.createUserFolder(null);
    assert.ok(rootFolder);

    harness.setPromptReply('几何专题');
    const midFolder = sandbox.createUserFolder(rootFolder.id);
    assert.ok(midFolder);

    harness.setPromptReply('折叠最值');
    const leafFolder = sandbox.createUserFolder(midFolder.id);
    assert.ok(leafFolder);

    // 在试卷 p1 和教材 b1 下直接新建子文件夹
    harness.setPromptReply('卷A一轮错题');
    const paperSub = sandbox.createUserFolder('paper:p1');
    assert.equal(paperSub.parentId, 'paper:p1');
    harness.setPromptReply('必修一压轴题');
    const bookSub = sandbox.createUserFolder('book:b1');
    assert.equal(bookSub.parentId, 'book:b1');

    // 向祖父与叶子分别投入题目（q2 重复放入两级，父级计数应自动去重为 3）
    sandbox.addQuestionsToUserFolder(['q1', 'q2'], rootFolder.id);
    sandbox.addQuestionsToUserFolder(['q2', 'q3'], leafFolder.id);

    const roots = sandbox.getFolderTreeRoots();
    const ufSection = roots.find((r) => r.id === 'section:folders');
    assert.ok(ufSection, '左侧树必须包含统一文件夹分组 section:folders');

    // 断言 section:folders 内全部使用文件夹图标（📁 或 📂），无旧文档/书本图标
    const walkIcons = (nodes) => {
      for (const n of nodes) {
        if (n.type !== 'hint') {
          assert.ok(n.icon === '📁' || n.icon === '📂', `节点 ${n.id} 必须统一为文件夹图标，实际为 ${n.icon}`);
        }
        if (n.children && n.children.length) walkIcons(n.children);
      }
    };
    walkIcons(ufSection.children);

    const rootNode = ufSection.children.find((c) => c.folderId === rootFolder.id);
    assert.equal(rootNode.count, 3, '父文件夹计数必须自动去重汇总自身与所有子文件夹的题目');
    assert.equal(rootNode.children[0].count, 2, '中间层级应汇总子文件夹的 2 道题');
    assert.equal(rootNode.children[0].children[0].count, 2);

    const papersGroup = ufSection.children.find((c) => c.id === 'group:papers');
    const paperP1Node = papersGroup.children.find((c) => c.id === 'paper:p1');
    assert.ok(paperP1Node.children.some((c) => c.folderId === paperSub.id), '试卷节点下必须能挂载并显示新建子文件夹');

    const booksGroup = ufSection.children.find((c) => c.id === 'group:books');
    const bookB1Node = booksGroup.children.find((c) => c.id === 'book:b1');
    assert.ok(bookB1Node.children.some((c) => c.folderId === bookSub.id), '教材节点下必须能挂载并显示新建子文件夹');

    // 点击父文件夹应能同时筛选到直属题和子孙题
    sandbox.renderFolderTree();
    sandbox.applyFolderNode('userfolder:' + rootFolder.id);
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()).sort(), ['q1', 'q2', 'q3']);

    // 防成环校验：不能把高三错题移入几何专题或折叠最值或自身
    assert.equal(sandbox.moveUserFolder(rootFolder.id, rootFolder.id), false);
    assert.equal(sandbox.moveUserFolder(rootFolder.id, midFolder.id), false);
    assert.equal(sandbox.moveUserFolder(rootFolder.id, leafFolder.id), false);

    // 将 leafFolder 提升为根文件夹（parentId = null），再检查计数变化
    assert.equal(sandbox.moveUserFolder(leafFolder.id, null), true);
    const rootsAfterMove = sandbox.getFolderTreeRoots().find((r) => r.id === 'section:folders').children.filter((c) => c.type === 'userfolder');
    assert.equal(rootsAfterMove.length, 2);
    assert.equal(rootsAfterMove.find((n) => n.folderId === rootFolder.id).count, 2);

    // 移出试题后，原题库 questionsData 数量不变，但文件夹中移出生效
    sandbox.removeQuestionsFromUserFolder(['q2'], rootFolder.id);
    assert.equal(sandbox.collectUserFolderQuestionIds(rootFolder.id).has('q2'), false);
    assert.equal(sandbox.__getQuestionsLength(), 3, '从文件夹移出试题绝不能删除原始题库题目');
  });

  await t.test('5. 删除提醒弹窗、统一回收站视图、恢复与彻底清理闭环验证', async () => {
    const harness = createEagleRuntimeSandbox();
    const { sandbox, elementsById } = harness;
    sandbox.__setTestData(
      [{ id: 'p1', name: '月考卷', published: 1, materialType: 'exam', createdAt: new Date() }],
      [{ id: 'q1', documentId: 'p1', questionNumber: 1, tags: [] }],
      []
    );

    harness.setPromptReply('函数专题');
    const parentF = sandbox.createUserFolder('paper:p1');
    harness.setPromptReply('导数子目录');
    const childF = sandbox.createUserFolder(parentF.id);
    sandbox.addQuestionsToUserFolder(['q1'], childF.id);

    // 删除 childF 前先弹警告弹窗
    sandbox.confirmDeleteUserFolder(childF.id);
    assert.equal(elementsById.get('deleteConfirmModal').classList.contains('hidden'), false);
    assert.match(elementsById.get('deleteConfirmImpactList').innerHTML, /1 道试题引用/);
    await sandbox.executePendingDelete();

    assert.equal(sandbox.readUserFolders().length, 1);
    assert.equal(sandbox.trashTotalCount(), 1);
    sandbox.renderFolderTree();
    assert.equal(elementsById.get('menuBadgeTrash').innerText, '1', '回收站计数应同步至左上角 ≡ 主菜单');

    // 点击主菜单中的回收站入口切换主视图到 #recycleBinPanel
    sandbox.applyFolderNode('trash:root');
    assert.equal(elementsById.get('recycleBinPanel').classList.contains('hidden'), false);
    assert.equal(elementsById.get('questionListContainer').classList.contains('hidden'), true);
    assert.match(elementsById.get('recycleBinListContainer').innerHTML, /导数子目录/);

    // 当原父文件夹 parentF 仍存在时，恢复 childF 应挂回 parentF；若先删掉 parentF，则回退挂载到根目录
    sandbox.confirmDeleteUserFolder(parentF.id);
    await sandbox.executePendingDelete();
    assert.equal(sandbox.readUserFolders().length, 0);
    assert.equal(sandbox.trashTotalCount(), 2);

    const childTrashEntry = sandbox.readLocalTrash().folders.find((f) => f.rootName === '导数子目录');
    sandbox.restoreTrashFolder(childTrashEntry.trashId);
    const restoredChild = sandbox.readUserFolders().find((f) => f.id === childF.id);
    assert.ok(restoredChild);
    assert.equal(restoredChild.parentId, null, '原父文件夹已不存在时恢复应自动兜底置于根目录');

    // 清空回收站需再次确认弹窗，确认后彻底清除
    sandbox.confirmEmptyRecycleBin();
    assert.equal(elementsById.get('deleteConfirmModal').classList.contains('hidden'), false);
    assert.match(elementsById.get('deleteConfirmTitle').innerText, /清空整个回收站/);
    await sandbox.executePendingDelete();
    assert.equal(sandbox.trashTotalCount(), 0);

    // 切回全部题库应隐藏回收站面板并恢复题卡容器
    sandbox.applyFolderNode('all');
    assert.equal(elementsById.get('recycleBinPanel').classList.contains('hidden'), true);
    assert.equal(elementsById.get('questionListContainer').classList.contains('hidden'), false);
  });

  await t.test('6. 右侧注释与 http:// 链接窗口、所属文件夹胶囊、系统导航排序、快速添加与复制/粘贴标签回归测试', async () => {
    // 静态结构防回退校验：确认不含原 1.3 顶部预览图和底部导出按钮，且包含新组件并保留 Ctrl+Shift+C
    assert.equal(eagleHtml.includes('id="inspectorTopThumb"'), false, '右侧栏不得出现 1.3 顶部扫描缩略图');
    assert.equal(eagleHtml.includes('id="btnInspectorExport"'), false, '右侧栏底部不得出现 1.3 单个导出按钮');
    assert.ok(eagleHtml.includes('id="inspectorNameInput"'), '右侧栏必须包含题目名称输入框');
    assert.ok(eagleHtml.includes('id="qlNameInput"'), '快速审题弹窗必须包含题目名称输入框');
    assert.ok(eagleHtml.includes('id="inspectorNoteInput"'), '右侧栏必须包含添加注释输入框');
    assert.ok(eagleHtml.includes('id="inspectorUrlInput"'), '右侧栏必须包含 http:// 链接输入框');
    assert.ok(eagleHtml.includes('id="inspectorFoldersContainer"'), '右侧栏必须包含所属文件夹胶囊容器');
    assert.ok(eagleHtml.includes('id="eagleAppMainMenu"'), '左上角必须包含桌面主菜单 #eagleAppMainMenu');
    assert.match(eagleHtml, /e\.ctrlKey && e\.shiftKey && \(e\.key === 'C' \|\| e\.key === 'c'\)/, '必须保留 Ctrl+Shift+C 复制试题图片快捷键');
    assert.match(eagleHtml, /copyQuestionTags/, '必须提供 Ctrl+Alt+C 复制考点标签能力');

    const harness = createEagleRuntimeSandbox();
    const { sandbox, elementsById } = harness;
    sandbox.__setTestData(
      [{ id: 'p1', name: '高三摸底卷', published: 1, materialType: 'exam', createdAt: new Date() }],
      [
        {
          id: 'q1',
          documentId: 'p1',
          questionNumber: 1,
          questionType: '解答题',
          source: '高三摸底卷',
          stemText: '已知函数 f(x) 在区间上单调递增',
          tags: [
            { id: 't1', name: '函数单调性', kind: 'knowledge', color: 'emerald' },
            { id: 't2', name: '导数应用', kind: 'method', color: 'sky' }
          ]
        },
        {
          id: 'q2',
          documentId: '',
          questionNumber: 2,
          questionType: '单选题',
          source: '零散题库',
          stemText: '求解不等式解集',
          tags: []
        }
      ],
      []
    );

    // 1. 建立用户文件夹并添加 q1 -> 应记录为最近使用文件夹，且左侧未分类视图计为 1 (仅 q2)
    harness.setPromptReply('压轴错题本');
    const folder = sandbox.createUserFolder(null);
    sandbox.addQuestionsToUserFolder(['q1'], folder.id);
    assert.equal(sandbox.getLastUsedUserFolder()?.id, folder.id, '放入文件夹后应记录上次使用的文件夹');

    // 验证左侧栏去重与折叠规范：核心保留 all / favorites / 智能文件夹 / 文件夹；其余专项视图、标签管理与回收站统一收纳进左上角 ≡ 主菜单
    const rootsBefore = sandbox.getFolderTreeRoots();
    const rootIds = rootsBefore.map((r) => r.id);
    assert.ok(rootIds.includes('all'), '左侧栏必须保留全部题库');
    assert.ok(rootIds.includes('smart:favorites'), '左侧栏必须保留我收藏的试题');
    assert.ok(!rootIds.includes('smart:uncategorized'), '未分类已收纳至左上角 ≡ 菜单，不应在左侧栏重复平铺');
    assert.ok(!rootIds.includes('smart:need_tags'), '待补充考点标签已收纳至左上角 ≡ 菜单，不应在左侧栏重复平铺');
    assert.ok(!rootIds.includes('smart:random'), '随机模式已收纳至左上角 ≡ 菜单，不应在左侧栏重复平铺');
    assert.ok(!rootIds.includes('action:tag_manager'), '标签管理已去重并统一至左上角 ≡ 菜单');
    assert.ok(!rootIds.includes('trash:root'), '回收站已统一至左上角 ≡ 菜单');
    assert.ok(rootIds.indexOf('section:custom') < rootIds.indexOf('section:folders'), '智能文件夹必须在文件夹分段之前');
    assert.equal(rootsBefore.find((r) => r.id === 'section:folders').folderCount, 2, '文件夹统计应为 1 个自定义文件夹 + 1 份试卷 = 2');

    // 验证分段标题格式含 (N) 及主菜单角标同步
    sandbox.renderFolderTree();
    assert.match(elementsById.get('folderTreeContainer').innerHTML, /文件夹 \(2\)/, '文件夹分段标题应渲染为 文件夹 (2)');
    assert.equal(elementsById.get('menuBadgeUncategorized').innerText, '1', 'q2 应计入主菜单未分类角标 (1)');

    // 2. 右侧栏单题聚焦：所属文件夹胶囊 + 注释与 http:// 链接保存及关键词搜索联动
    sandbox.focusQuestion('q1');
    assert.equal(elementsById.get('inspectorEagleTopMeta').classList.contains('hidden'), false, '聚焦单题时应展示注释/URL/文件夹区块');
    assert.match(elementsById.get('inspectorFoldersContainer').innerHTML, /压轴错题本/, '右侧栏必须显示 q1 所属文件夹胶囊');

    elementsById.get('inspectorNameInput').value = '隐零点压轴母题';
    elementsById.get('inspectorNoteInput').value = '洛必达与隐零点放缩技巧';
    elementsById.get('inspectorUrlInput').value = 'https://www.zhihu.com/question/12345';
    sandbox.saveInspectorNoteAndUrl();

    const savedAnn = sandbox.getQuestionAnnotation('q1');
    assert.equal(savedAnn.name, '隐零点压轴母题');
    assert.equal(elementsById.get('inspectorTitle').innerText, '隐零点压轴母题', '保存题目名称后右侧检查器标题应同步更新');
    assert.equal(savedAnn.note, '洛必达与隐零点放缩技巧');
    assert.equal(savedAnn.url, 'https://www.zhihu.com/question/12345');
    assert.equal(elementsById.get('inspectorUrlActions').classList.contains('hidden'), false, '输入链接后应显示跳转与复制按钮');

    // 搜索题目名称、注释内容与 URL 均可命中 q1
    sandbox.__getFilterState().keyword = '隐零点压轴母题';
    sandbox.applyFilters();
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1'], '全局搜索应命中右侧题目名称');

    sandbox.__getFilterState().keyword = '隐零点放缩';
    sandbox.applyFilters();
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1'], '全局搜索应命中右侧注释文本');

    sandbox.__getFilterState().keyword = 'zhihu.com/question/12345';
    sandbox.applyFilters();
    assert.deepEqual(JSON.parse(sandbox.__getFilteredIds()), ['q1'], '全局搜索应命中右侧 URL 链接');
    sandbox.__getFilterState().keyword = '';
    sandbox.applyFilters();

    // 3. Shift+D 快速添加到上次使用的文件夹 + Ctrl+Alt+C/V 复制与粘贴标签
    sandbox.addQuestionsToLastUsedFolder(['q2']);
    assert.equal(elementsById.get('menuBadgeUncategorized').innerText, '0', 'q2 归档后主菜单未分类计数应归零');

    const copied = sandbox.copyQuestionTags('q1');
    assert.equal(copied.length, 2);
    const pastedQuestions = await sandbox.pasteQuestionTags(['q2']);
    assert.equal(pastedQuestions, 1, 'pasteQuestionTags 应将标签粘贴到 1 道目标试题');
    assert.equal(
      elementsById.get('menuBadgeNeedTags').innerText,
      '0',
      'q2 粘贴标签后主菜单待补充考点标签计数应归零'
    );

    // 4. 左侧栏收起/展开 + 左上角主菜单 + 随机模式切换
    sandbox.toggleLeftSidebar();
    assert.equal(elementsById.get('leftFolderSidebar').classList.contains('collapsed'), true);
    assert.equal(elementsById.get('btnReopenLeftSidebar').classList.contains('hidden'), false);
    assert.equal(elementsById.get('btnCollapsedAppMenu').classList.contains('hidden'), false);
    sandbox.toggleLeftSidebar();
    assert.equal(elementsById.get('leftFolderSidebar').classList.contains('collapsed'), false);
    assert.equal(elementsById.get('btnCollapsedAppMenu').classList.contains('hidden'), true);

    elementsById.get('eagleAppMainMenu').classList.add('hidden');
    sandbox.toggleEagleAppMainMenu();
    assert.equal(elementsById.get('eagleAppMainMenu').classList.contains('hidden'), false);
    sandbox.toggleEagleAppMainMenu();
    assert.equal(elementsById.get('eagleAppMainMenu').classList.contains('hidden'), true);

    sandbox.applyFolderNode('smart:random');
    assert.equal(sandbox.__getFilterState().smartFilter, 'random', '点击随机模式应激活 random 视图');
  });

  await t.test('7. 文件夹全链路修复：右侧默认显示试卷胶囊、支持移出/放入试卷与挂载子文件夹、展开旧缓存迁移', () => {
    // 1. 模拟旧版 localStorage（仅含 section:userfolders / section:library，不含 section:folders），应自动迁移展开
    const legacyHarness = createEagleRuntimeSandbox({
      localStorage: {
        eagle_folder_tree_expanded: JSON.stringify(['all', 'section:userfolders', 'section:custom', 'section:library'])
      }
    });
    const sb = legacyHarness.sandbox;
    const els = legacyHarness.elementsById;

    assert.equal(sb.__hasExpandedNode('section:folders'), true, '旧版缓存迁移后应默认展开 section:folders');
    assert.equal(sb.__hasExpandedNode('group:papers'), true, '旧版缓存迁移后应默认展开 group:papers');
    assert.equal(sb.__hasExpandedNode('group:books'), true, '旧版缓存迁移后应默认展开 group:books');

    sb.__setTestData(
      [{ id: 'p1', name: '高三摸底卷', published: 1, materialType: 'exam', createdAt: new Date() }],
      [
        { id: 'q1', documentId: 'p1', questionNumber: 1, questionType: '解答题', source: '高三摸底卷', tags: [] },
        { id: 'q2', documentId: '', questionNumber: 2, questionType: '单选题', source: '零散题库', tags: [] }
      ],
      []
    );

    // 2. 聚焦 q1 时，即使尚未新建自定义文件夹，右侧栏也必须直接显示其所属试卷胶囊「高三摸底卷」
    sb.focusQuestion('q1');
    const pillsHtml = els.get('inspectorFoldersContainer').innerHTML;
    assert.match(pillsHtml, /高三摸底卷/, '右侧栏必须显示题目默认所属的试卷文件夹胶囊');

    // q1 因属于高三摸底卷而不算未分类；q2 没有所属文件夹也不在任何试卷中，计为 1
    assert.equal(sb.isQuestionUncategorized('q1'), false);
    assert.equal(sb.isQuestionUncategorized('q2'), true);
    sb.renderFolderTree();
    assert.equal(els.get('menuBadgeUncategorized').innerText, '1');

    // 3. 在右侧栏点击 × 将 q1 从试卷文件夹「高三摸底卷」移出后，应变为未分类（且不删原题），计数回落
    sb.removeQuestionsFromDocFolder(['q1'], 'p1');
    assert.equal(sb.isQuestionUncategorized('q1'), true, '从试卷文件夹移出且无其他归属后应记入未分类');
    assert.equal(els.get('menuBadgeUncategorized').innerText, '2');
    assert.match(els.get('inspectorFoldersContainer').innerHTML, /未归入文件夹/, '移出后右侧栏应给出未归入提示并保留 + 按钮');

    sb.renderFolderTree();
    sb.applyFolderNode('paper:p1');
    assert.deepEqual(JSON.parse(sb.__getFilteredIds()), [], '移出后切换至高三摸底卷应不再包含 q1');

    // 4. 在 paper:p1 下新建子文件夹后，打开 #folderPickerModal 必须能看到「高三摸底卷」以及挂载在其下的子文件夹
    legacyHarness.setPromptReply('卷一压轴子夹');
    const paperSub = sb.createUserFolder('paper:p1');
    sb.openFolderPickerForQuestions(['q1', 'q2']);
    const pickerHtml = els.get('folderPickerTreeList').innerHTML;
    assert.match(pickerHtml, /高三摸底卷/, '文件夹选择器必须显示试卷文件夹');
    assert.match(pickerHtml, /卷一压轴子夹/, '文件夹选择器必须显示挂载在试卷下的子文件夹');

    // 5. 通过选择器将 q1 重新放回 paper:p1，并将 q2 放入 paper:p1 -> 两者恢复归属，未分类归零，左侧展开链路完整
    sb.selectFolderFromPicker('paper:p1');
    assert.equal(sb.getLastUsedUserFolder()?.id, 'paper:p1', '放入试卷文件夹应记录为最近使用的文件夹');
    assert.equal(sb.isQuestionUncategorized('q1'), false);
    assert.equal(sb.isQuestionUncategorized('q2'), false);
    assert.equal(els.get('menuBadgeUncategorized').innerText, '0');

    sb.focusQuestion('q1');
    assert.match(els.get('inspectorFoldersContainer').innerHTML, /高三摸底卷/);
    sb.applyFolderNode('userfolder:' + paperSub.id);
    assert.equal(sb.__hasExpandedNode('section:folders'), true);
    assert.equal(sb.__hasExpandedNode('group:papers'), true);
    assert.equal(sb.__hasExpandedNode('paper:p1'), true);
  });

  await t.test('8. 文件夹 4 层深度上限、右栏胶囊最末级显示+完整路径悬停、默认展开至第 2 层', () => {
    const harness = createEagleRuntimeSandbox();
    const sb = harness.sandbox;
    const els = harness.elementsById;

    const papers = [
      { id: 'p1', name: '高三摸底卷', published: 1, materialType: 'exam', createdAt: new Date() },
      { id: 'b1', name: '高中数学（A版）必修第一册.pdf', published: 2, materialType: 'book', createdAt: new Date() }
    ];
    const questions = [
      { id: 'q1', documentId: 'p1', questionNumber: 1, questionType: '解答题', source: '高三摸底卷', tags: [] },
      {
        id: 'qb1',
        documentId: 'b1',
        paperPage: 12,
        section: '习题1.2',
        questionNumber: 1,
        questionType: '单选题',
        source: '高中数学（A版）必修第一册.pdf',
        tags: []
      },
      {
        id: 'qb2',
        documentId: 'b1',
        paperPage: 15,
        section: '习题1.3',
        questionNumber: 2,
        questionType: '填空题',
        source: '高中数学（A版）必修第一册.pdf',
        tags: []
      }
    ];
    sb.__setTestData(papers, questions, []);
    sb.renderFolderTree();

    // 1. 默认展开至第 2 层：section:folders / group:papers / group:books 展开，第 2 层试卷与教材直接可见；第 3/4 层章/小节默认收起
    assert.equal(sb.__hasExpandedNode('section:folders'), true);
    assert.equal(sb.__hasExpandedNode('group:papers'), true);
    assert.equal(sb.__hasExpandedNode('group:books'), true);
    assert.equal(sb.__hasExpandedNode('paper:p1'), false, '第 2 层试卷节点自身默认不展开第 3 层');
    assert.equal(sb.__hasExpandedNode('book:b1'), false, '第 2 层教材节点自身默认不展开第 3 层章节点');
    assert.equal(sb.__hasExpandedNode('bookchap:b1:1'), false, '第 3 层章节点默认不展开第 4 层小节');
    const initialTreeHtml = els.get('folderTreeContainer').innerHTML;
    assert.match(initialTreeHtml, /高三摸底卷/, '初始树应直接展示第 2 层试卷名');
    assert.match(initialTreeHtml, /A版 · 必修第一册/, '初始树应直接展示第 2 层教材名');
    assert.doesNotMatch(initialTreeHtml, /习题1\.2/, '第 4 层小节默认应处于收起状态');

    // 2. 右侧栏教材胶囊仅展示最末级名称 + 悬停完整路径 + 点击直达第 4 层小节
    sb.focusQuestion('qb1');
    const bookInspectorHtml = els.get('inspectorFoldersContainer').innerHTML;
    assert.match(bookInspectorHtml, />📁 习题1\.2</, '教材题右侧胶囊应仅展示最末级小节名');
    assert.doesNotMatch(bookInspectorHtml, />📁 A版 · 必修第一册</, '教材题有小节时不应只展示顶层书名');
    assert.match(bookInspectorHtml, /title="教材版本 \/ A版 · 必修第一册 \/ 第1章 \/ 习题1\.2（点击定位）"/, '悬停提示必须包含完整层级路径');

    sb.navigateToInspectorBookTarget('b1', '1', '习题1.2');
    assert.equal(sb.__hasExpandedNode('book:b1'), true);
    assert.equal(sb.__hasExpandedNode('bookchap:b1:1'), true);
    assert.match(sb.__getActiveNodeId(), /^booksec:b1:1:/, '点击教材小节胶囊应激活对应 booksec 节点');
    assert.deepEqual(JSON.parse(sb.__getFilteredIds()), ['qb1']);

    // 3. 验证普通文件夹 4 层深度上限（新建 + 移动 + 移动弹窗禁用提示）
    assert.equal(sb.MAX_FOLDER_DEPTH, 4);
    harness.setPromptReply('L1错题');
    const l1 = sb.createUserFolder(null);
    harness.setPromptReply('L2专题');
    const l2 = sb.createUserFolder(l1.id);
    harness.setPromptReply('L3折叠分支');
    const l3 = sb.createUserFolder(l2.id);
    harness.setPromptReply('L4最深叶夹');
    const l4 = sb.createUserFolder(l3.id);
    assert.ok(l1 && l2 && l3 && l4, '顶层 1~4 层文件夹应均创建成功');
    assert.equal(sb.getFolderParentDepth(l4.id, sb.readUserFolders(), false), 4);

    harness.setPromptReply('L5超限夹');
    const l5 = sb.createUserFolder(l4.id);
    assert.equal(l5, null, '第 4 层下禁止继续新建第 5 层子文件夹');
    assert.ok(harness.toasts.some((x) => /4 层/.test(x.msg)), '超限时应提示最多 4 层警告');

    // 在试卷（第 2 层）下新建第 3 层 P_L3 和第 4 层 P_L4，尝试建第 5 层被拒
    harness.setPromptReply('卷内L3');
    const pL3 = sb.createUserFolder('paper:p1');
    harness.setPromptReply('卷内L4');
    const pL4 = sb.createUserFolder(pL3.id);
    assert.ok(pL3 && pL4, '试卷下可挂第 3 层及第 4 层子文件夹');
    harness.setPromptReply('卷内L5');
    assert.equal(sb.createUserFolder(pL4.id), null, '试卷下子文件夹同样不得超过第 4 层');

    // 移动超限校验：l3 子树高度为 2，移到第 3 层的 pL3 下总深 3+2=5>4 应被拒绝；移到第 2 层 paper:p1 下总深 2+2=4 应成功
    assert.equal(sb.moveUserFolder(l3.id, pL3.id), false, '移动后总层数超过 4 层应拒绝移动');
    sb.openFolderPickerForMove('user', l3.id);
    assert.match(els.get('folderPickerTreeList').innerHTML, /卷内L3（超过4层上限）/, '移动弹窗中超限父文件夹应不可选并标注原因');
    sb.closeFolderPickerModal();

    assert.equal(sb.moveUserFolder(l3.id, 'paper:p1'), true, '移动到第 2 层试卷节点下（2+2=4 层）应允许');

    // 验证智能文件夹同样受 4 层深度上限约束
    harness.setPromptReply('S1');
    const s1 = sb.saveCurrentAsSmartFolder(null);
    harness.setPromptReply('S2');
    const s2 = sb.saveCurrentAsSmartFolder(s1.id);
    harness.setPromptReply('S3');
    const s3 = sb.saveCurrentAsSmartFolder(s2.id);
    harness.setPromptReply('S4');
    const s4 = sb.saveCurrentAsSmartFolder(s3.id);
    assert.ok(s1 && s2 && s3 && s4);
    harness.setPromptReply('S5');
    assert.equal(sb.saveCurrentAsSmartFolder(s4.id), null, '智能文件夹也不得超过 4 层');

    harness.setPromptReply('S1b');
    const s1b = sb.saveCurrentAsSmartFolder(null);
    harness.setPromptReply('S2b');
    const s2b = sb.saveCurrentAsSmartFolder(s1b.id);
    harness.setPromptReply('S3b');
    const s3b = sb.saveCurrentAsSmartFolder(s2b.id);
    assert.equal(sb.moveCustomSmartFolder(s3.id, s3b.id), false, '智能文件夹移动后超 4 层应被拦截');

    // 4. 同分支去重 + 最末级自定义文件夹胶囊 + 悬停完整路径
    sb.addQuestionsToUserFolder(['q1'], l3.id);
    sb.addQuestionsToUserFolder(['q1'], l4.id);
    sb.focusQuestion('q1');
    const q1InspectorHtml = els.get('inspectorFoldersContainer').innerHTML;
    assert.match(q1InspectorHtml, />📁 L4最深叶夹</, '同分支下右侧胶囊应仅展示最末级文件夹名');
    assert.doesNotMatch(q1InspectorHtml, />📁 L3折叠分支</, '同分支祖先文件夹不应重复显示');
    assert.doesNotMatch(q1InspectorHtml, />📁 高三摸底卷</, '已归入挂载子文件夹时不应重复显示父级试卷');
    assert.match(q1InspectorHtml, /title="试卷 \/ 高三摸底卷 \/ L3折叠分支 \/ L4最深叶夹（点击定位）"/, '悬停应展示包含试卷与各级子夹的完整路径');
  });

  await t.test('标签浮层搜索要按名字、别名和拼音收列表', () => {
    const { sandbox, elementsById } = createEagleRuntimeSandbox();
    vm.runInContext(`
      tagCatalogue = [
        { id: 'k1', name: '空间向量', kind: 'knowledge', aliases: [], pinyin: 'kongjianxiangliang', initials: 'kjxl', search_keys: ['空间向量', 'kongjianxiangliang', 'kjxl'], active: true },
        { id: 'k2', name: '一元一次不等式', kind: 'knowledge', aliases: [], pinyin: 'yiyuanyici', initials: 'yyyc', search_keys: ['一元一次不等式', 'yiyuanyici', 'yyyc'], active: true },
        { id: 'm1', name: '数形结合', kind: 'method', aliases: ['立体'], pinyin: 'shuxingjiehe', initials: 'sxjh', search_keys: ['数形结合', '立体', 'shuxingjiehe', 'sxjh'], active: true }
      ];
      tagGroupsData.forEach(g => { g.tags = []; });
      tagCatalogue.forEach(t => {
        const group = t.kind === 'method' ? tagGroupsData[1] : tagGroupsData[0];
        group.tags.push({ id: t.id, name: t.name, color: group.color, useCount: 0, isPinned: false });
      });
      document.getElementById('dropTagSearchInput').value = '空间';
      renderTagFilterDropdownList();
    `, sandbox);
    const list = elementsById.get('dropTagListContainer');
    assert.match(list.innerHTML, /空间向量/, '中文搜索要命中名字里带这个词的标签');
    assert.doesNotMatch(list.innerHTML, /一元一次不等式/, '对不上的标签不能继续铺在列表里');
    assert.doesNotMatch(list.innerHTML, /数形结合/, '别的分组里对不上的标签也要收掉');
    assert.doesNotMatch(list.innerHTML, /解题思想与方法/, '一个标签都没中的分组标题不要留着');

    vm.runInContext(`
      document.getElementById('dropTagSearchInput').value = 'kjxl';
      renderTagFilterDropdownList();
    `, sandbox);
    assert.match(list.innerHTML, /空间向量/, '拼音首字母也要能搜到，字段在目录上不在分组对象上');
    assert.doesNotMatch(list.innerHTML, /一元一次不等式/);

    vm.runInContext(`
      document.getElementById('dropTagSearchInput').value = '立体';
      renderTagFilterDropdownList();
    `, sandbox);
    assert.match(list.innerHTML, /数形结合/, '别名在目录的 search_keys 里，搜索必须回目录查');
    assert.doesNotMatch(list.innerHTML, /空间向量/);

    vm.runInContext(`
      document.getElementById('dropTagSearchInput').value = '没有这个标签';
      renderTagFilterDropdownList();
    `, sandbox);
    assert.match(list.innerHTML, /没有匹配/, '一个都对不上时要说明，而不是留空或继续铺全表');

    vm.runInContext(`
      document.getElementById('dropTagSearchInput').value = '';
      renderTagFilterDropdownList();
    `, sandbox);
    assert.match(list.innerHTML, /空间向量/);
    assert.match(list.innerHTML, /一元一次不等式/);
    assert.match(list.innerHTML, /数形结合/, '清空搜索后三个分组里的标签都要回来');
  });
});
