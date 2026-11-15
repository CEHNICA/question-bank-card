// @ts-check
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 1.26.53：题库工作台「快速联想打标框」回归测试。
//
// 这一块以前是纯装饰品：输入框的 id 在 HTML 里，但全代码库没有任何一处给它
// 绑过事件，联想下拉从来没被填过，全局 keydown 里也没有 T 分支。
// 结果就是老师打「李孟德」按 Enter 什么都不会发生，而且没有任何测试会红。
//
// 这个套件盯住四个环节，缺一不可：
//   1) 输入框真的绑了事件（input / keydown / click）
//   2) 联想下拉真的会被填（渲染函数存在且被调用）
//   3) Enter / 方向键 / T 快捷键三条通路都在
//   4) 后端真的会把 pinyin 字段吐出来（前端拼不出拼音）

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const eagleHtml = fs.readFileSync(path.join(__dirname, 'eagle', 'index.html'), 'utf8');
const filterBarJs = fs.readFileSync(path.join(__dirname, 'eagle-filter-bar.js'), 'utf8');
const taggingPy = fs.readFileSync(
  path.join(__dirname, '..', 'backend', 'core', 'library_tagging.py'),
  'utf8',
);
const pinyinPy = fs.readFileSync(
  path.join(__dirname, '..', 'backend', 'core', 'tag_pinyin.py'),
  'utf8',
);

test('Eagle 快速联想打标框：输入框不再是装饰品', async (t) => {
  await t.test('1. 输入框与下拉容器的 HTML 结构在', () => {
    assert.match(eagleHtml, /id="tagAutocompleteInput"/, '必须存在 #tagAutocompleteInput');
    assert.match(eagleHtml, /id="tagSuggestDropdown"/, '必须存在 #tagSuggestDropdown');
    assert.match(
      eagleHtml,
      /id="tagAutocompleteInput"[\s\S]{0,400}id="tagSuggestDropdown"/,
      '下拉必须紧跟输入框（相对定位容器内），否则会飘到别处'
    );
  });

  await t.test('2. 输入框真的绑了事件（这是原来缺的那一环）', () => {
    const bindFn = eagleHtml.match(/function bindTagAutocomplete\(\)\s*\{([\s\S]*?)\n    \}/);
    assert.ok(bindFn, '必须定义 bindTagAutocomplete()');
    const body = bindFn[1];
    assert.match(body, /addEventListener\('input'/, '必须监听 input 事件，否则输入不出联想');
    assert.match(body, /addEventListener\('keydown'/, '必须监听 keydown，否则 Enter 贴不了标');
    assert.match(body, /addEventListener\('focus'/, '必须监听 focus，点进去就该出候选');
    assert.match(body, /addEventListener\('click'/, '下拉必须能点选');
    assert.match(body, /addEventListener\('blur'/, '失焦必须收起下拉');

    // 事件绑定函数必须真的被调用过一次
    assert.match(
      eagleHtml,
      /setupEventListeners\(\);\s*\n\s*bindTagAutocomplete\(\);/,
      'init() 必须调用 bindTagAutocomplete()，否则绑了也白绑'
    );
  });

  await t.test('3. 联想下拉会被真的填上内容', () => {
    assert.match(
      eagleHtml,
      /function renderTagSuggestDropdown\(\)/,
      '必须有 renderTagSuggestDropdown()'
    );
    const renderFn = eagleHtml.match(/function renderTagSuggestDropdown\(\)\s*\{([\s\S]*?)\n    \}/);
    assert.ok(renderFn, 'renderTagSuggestDropdown() 必须有实现');
    assert.match(renderFn[1], /tagSuggestDropdown/, '必须往 #tagSuggestDropdown 里写');
    assert.match(renderFn[1], /data-tag-index/, '候选项必须带 data-tag-index，供点击取值');
    assert.match(renderFn[1], /classList\.remove\('hidden'\)/, '填完必须把下拉显示出来');
  });

  await t.test('4. Enter / 方向键 / T 快捷键三条通路都在', () => {
    // Enter 提交
    assert.match(
      eagleHtml,
      /if \(evt\.key === 'Enter'\)[\s\S]{0,200}commitTagSuggest\(\)/,
      'Enter 必须走 commitTagSuggest()'
    );
    // 方向键导航
    assert.match(eagleHtml, /evt\.key === 'ArrowDown'/, '必须支持 ArrowDown');
    assert.match(eagleHtml, /evt\.key === 'ArrowUp'/, '必须支持 ArrowUp');
    assert.match(eagleHtml, /function moveTagSuggestHighlight\(/, '必须有高亮移动函数');
    // T 快捷键
    assert.match(
      eagleHtml,
      /if \(e\.key === 't' \|\| e\.key === 'T'\)[\s\S]{0,200}focusTagAutocompleteInput\(\)/,
      '全局 keydown 必须有 T 分支（快捷键指南里一直写着 T）'
    );
    // 右键菜单「关联/修改标签」也走同一个函数
    assert.match(
      eagleHtml,
      /onclick="focusTagAutocompleteInput\(\); hideEagleContextMenu\(\);"/,
      '右键菜单必须调用 focusTagAutocompleteInput()'
    );
  });

  await t.test('5. 目录里没有的名字有「新建标签」出口（不然照样打不上）', () => {
    assert.match(eagleHtml, /function createTagAndApply\(/, '必须有 createTagAndApply()');
    assert.match(eagleHtml, /data-tag-create/, '下拉必须给出「新建」按钮');
    assert.match(
      eagleHtml,
      /action: 'create_tag'/,
      '新建必须走后端 create_tag 动作，不能只在本地造一个假标签'
    );
    // 后端 create_tag 返回的是整份 catalogue，不是单个标签 —— 早先按 after.tag 取，
    // 拿到 undefined，标签建出来了却贴不到题上。这个坑不能踩第二次。
    assert.match(
      eagleHtml,
      /data\.catalogue\s*&&\s*Array\.isArray\(data\.catalogue\.tags\)/,
      '必须从 response.catalogue.tags 里取新标签'
    );
  });

  await t.test('6. 后端真的吐出 pinyin（前端自己拼不出拼音）', () => {
    assert.match(
      taggingPy,
      /"pinyin":\s*tag_pinyin\.full_pinyin\(tag\.name\)/,
      'tag_json 必须带 pinyin 字段'
    );
    assert.match(
      taggingPy,
      /"initials":\s*tag_pinyin\.initials\(tag\.name\)/,
      'tag_json 必须带 initials 字段（首字母搜索）'
    );
    assert.match(
      taggingPy,
      /"search_keys":\s*tag_pinyin\.search_keys\(/,
      'tag_json 必须带 search_keys（中文名/别名/全拼/首字母一把梭）'
    );
    assert.match(
      taggingPy,
      /from \. import tag_pinyin/,
      'library_tagging 必须导入 tag_pinyin'
    );
    // 拼音模块本身：缺依赖时要能降级，不能连带打标功能一起崩
    assert.match(pinyinPy, /except Exception/, 'pypinyin 缺失时必须静默降级');
    assert.match(pinyinPy, /def full_pinyin/, '必须提供 full_pinyin()');
    assert.match(pinyinPy, /def initials/, '必须提供 initials()');
    assert.match(pinyinPy, /def search_keys/, '必须提供 search_keys()');
  });

  await t.test('7. 前端拼音匹配用的是后端给的字段', () => {
    // 联想框
    assert.match(
      eagleHtml,
      /function matchTagByQuery\(tag, query\)/,
      '必须有 matchTagByQuery()'
    );
    assert.match(
      eagleHtml,
      /tag\.search_keys/,
      '联想匹配必须用 search_keys'
    );
    assert.match(eagleHtml, /tag\.pinyin/, '联想匹配必须兼容 pinyin');
    assert.match(eagleHtml, /tag\.initials/, '联想匹配必须兼容 initials');
    // 筛选栏
    assert.match(
      filterBarJs,
      /tag\.search_keys/,
      '筛选栏的标签搜索也必须用 search_keys（原来只读 tag.pinyin，而后端从不写它）'
    );
    // 刷新目录后拼音字段不能丢 —— 正好是最需要它的时候
    const reloadFn = eagleHtml.match(/async function reloadTagCatalogue\(\)\s*\{([\s\S]*?)\n    \}/);
    assert.ok(reloadFn, '必须定义 reloadTagCatalogue()');
    assert.match(reloadFn[1], /pinyin: t\.pinyin/, 'reloadTagCatalogue 必须带上 pinyin');
    assert.match(reloadFn[1], /initials: t\.initials/, 'reloadTagCatalogue 必须带上 initials');
    assert.match(
      reloadFn[1],
      /search_keys: t\.search_keys/,
      'reloadTagCatalogue 必须带上 search_keys'
    );
  });

  await t.test('8. 目录被别处改动时要自动重试，而不是甩一句「请刷新」', () => {
    // 1.26.53：目录是乐观锁（后端比对 revision）。页面开着不动的时候，
    // 别人改了目录，前端手里的 revision 就过期，后端回 409。
    // 用户看到「请刷新后重试」等于白搭 —— 自己拉最新目录重试一次就行。
    assert.match(
      eagleHtml,
      /if \(res\.status === 409\)[\s\S]{0,400}reloadTagCatalogue\(\)/,
      '新建标签遇到 409 必须先 reloadTagCatalogue() 再重试'
    );
    const createFn = eagleHtml.match(
      /async function createTagAndApply\([^)]*\)\s*\{([\s\S]*?)\n    \}/,
    );
    assert.ok(createFn, '必须定义 createTagAndApply()');
    assert.match(
      createFn[1],
      /res\.status === 409/,
      'createTagAndApply 必须处理 409'
    );
    assert.match(
      createFn[1],
      /JSON\.stringify\(buildBody\(\)\)/,
      '重试时必须重新构造请求体（revision 与 request_id 都要换新的）'
    );
    assert.match(
      createFn[1],
      /function buildBody|const buildBody/,
      '必须把请求体抽成 buildBody()，重试才能带上新的 revision'
    );
  });
});
