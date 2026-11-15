// @ts-check
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const eagleHtmlPath = path.join(__dirname, 'eagle', 'index.html');
const eagleHtml = fs.readFileSync(eagleHtmlPath, 'utf8');

test('Eagle 收藏体验与难度视觉解耦验证', async (t) => {
  await t.test('1. 视觉符号彻底解耦：评分为金色五角星，收藏全面升级为统一矢量 SVG 爱心（并移除顶栏重复入口）', () => {
    // 顶栏去重：收藏入口统一收口到左侧边栏 smart:favorites，不再在顶栏重复放 #headerFavBtn
    assert.doesNotMatch(eagleHtml, /id="headerFavBtn"/, '顶栏不应再包含重复的收藏按钮 #headerFavBtn');
    assert.doesNotMatch(eagleHtml, /id="headerFavoritesCount"/, '顶栏不应再包含重复的收藏计数器 #headerFavoritesCount');

    // 统一矢量 SVG 爱心辅助函数
    assert.match(eagleHtml, /function getHeartIconSvg\(/, '必须定义 getHeartIconSvg() 统一渲染全站矢量爱心图标');

    // 大卡片与紧凑行
    assert.match(eagleHtml, /id="star_btn_\$\{q\.id\}"[\s\S]*?\$\{getHeartIconSvg\(isFav/, '大题卡收藏按钮必须使用统一矢量爱心 SVG');
    assert.match(eagleHtml, /id="compact_fav_btn_\$\{q\.id\}"[\s\S]*?\$\{getHeartIconSvg\(isFav/, '紧凑行收藏按钮必须使用统一矢量爱心 SVG');

    // 评分保持五角星
    assert.match(eagleHtml, /q-difficulty-stars/, '题卡保留独立的 q-difficulty-stars 星级容器');
    assert.match(eagleHtml, /'★'\.repeat/, '星级渲染必须使用 ★ 五角星');

    // 批量操作栏与右键菜单
    assert.match(eagleHtml, /onclick="batchToggleFavorite\(\)"[\s\S]*?<svg[^>]*>[\s\S]*?<span>加入收藏<\/span>/, '批量操作栏加入收藏必须使用统一矢量爱心 SVG');
    assert.match(eagleHtml, /onclick="toggleFavorite\('\$\{q\.id\}'\); hideEagleContextMenu\(\);"[\s\S]*?getHeartIconSvg\(isFav/, '右键菜单收藏操作必须使用统一矢量爱心 SVG');

    // 右侧详情页属性栏去重：收藏开关已统一保留在题卡与紧凑行，不在右侧属性栏重复显示
    assert.doesNotMatch(eagleHtml, /id="metaFavoriteBtn"/, '右侧详情页考题属性卡片不应再重复显示试题收藏开关 #metaFavoriteBtn');
  });

  await t.test('2. 解决“我收藏的题目去哪了”：左侧分类树第一屏常驻置顶“我收藏的试题”', () => {
    // getFolderTreeRoots 中第二项为 smart:favorites，无论数量是否为 0 均常驻
    const rootsFnMatch = eagleHtml.match(/function getFolderTreeRoots\(\)\s*\{([\s\S]*?)return roots;/);
    assert.ok(rootsFnMatch, '必须定义 getFolderTreeRoots()');
    const rootsBody = rootsFnMatch[1];

    assert.match(rootsBody, /id:\s*'smart:favorites'/, '树根节点必须包含 smart:favorites');
    assert.match(rootsBody, /icon:\s*getHeartIconSvg\(true/, '树根中我收藏的试题图标必须使用统一矢量爱心 SVG');
    assert.match(rootsBody, /roots\.push\(\{\s*id:\s*'smart:favorites'[\s\S]*?count:\s*favoritesSet\.size/, '我收藏的试题必须直接 push 置顶常驻，不受 count > 0 限制');
  });

  await t.test('3. 收藏与筛选即时联动函数全链路存在', () => {
    assert.match(eagleHtml, /function filterByFavorites\(\)/, '必须提供顶栏 filterByFavorites() 函数');
    assert.match(eagleHtml, /function updateFavoritesBadge\(\)/, '必须提供 updateFavoritesBadge() 函数');
    assert.match(eagleHtml, /function renderInspectorFavoriteState\(/, '必须提供 renderInspectorFavoriteState 函数');

    // toggleFavorite 会触发 updateFavoritesBadge, renderFolderTree 和按需 applyFilters
    const toggleMatch = eagleHtml.match(/function toggleFavorite\(qid\)\s*\{([\s\S]*?)\n    \}/);
    assert.ok(toggleMatch, '必须定义 toggleFavorite');
    const toggleBody = toggleMatch[1];
    assert.match(toggleBody, /updateFavoritesBadge\(\)/, '切换收藏后必须刷新徽标');
    assert.match(toggleBody, /renderFolderTree\(\)/, '切换收藏后必须刷新左侧分类树数字');
    assert.match(toggleBody, /renderInspectorFavoriteState/, '切换收藏后必须联动右侧详情页开关');
    assert.match(toggleBody, /filterState\.smartFilter === 'favorites'[\s\S]*?applyFilters\(\)/, '收藏视图下取消收藏必须实时移出列表');
  });

  await t.test('4. 收藏筛选友好空态引导', () => {
    assert.match(eagleHtml, /isFavFilter \? `[\s\S]*?暂无收藏的试题[\s\S]*?返回浏览全部题库/, '收藏视图为空时必须展示专属友好引导空态，帮助老师了解如何收藏和返回');
  });
});
