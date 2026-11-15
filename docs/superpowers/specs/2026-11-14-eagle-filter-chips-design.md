# 题有据 Eagle 工作台 · 筛选条呈现重做

- 日期：2026-11-14
- 范围：`frontend/eagle/index.html`
- 状态：设计已定（用户拍板：形态 A / 难度精确+及以上 / chip 独占第二行）

## 1. 问题

用户在筛选胶囊上勾选了标签、题型、难度星级、解析与答案，但筛选条右侧只出现了一个 `5★`。

实锤根因（`renderInlineActiveChips()`，`index.html:4352`）：

| 筛选条件 | 状态变量 | 过滤是否生效 | 是否渲染 chip |
|---|---|---|---|
| 项目 | `paperId` | 是 | 是 |
| 文件夹 | `folderType/folderTagIds` | 是 | 是 |
| 智能文件夹 | `smartFilter` | 是 | 是 |
| 章节 | `chapter` | 是 | 是 |
| 难度星级 | `difficultyStars` | 是 | 是 |
| **标签筛选** | `selectedTagIds` / `excludedTagIds` | **是** | **否** |
| **题型** | `questionTypes` | **是** | **否** |
| **解析与答案** | `hasAnswer` / `hasAnalysis` | **是** | **否** |

过滤确实生效（结果数会变），但三类条件一个 chip 都不渲染 —— 用户看到的就是「勾了一堆，只剩一个 5★」。

## 2. 连带缺陷（同一批修）

| # | 缺陷 | 位置 |
|---|---|---|
| D1 | 胶囊文字永不更新：`pillLabelTag/Type/Star/Status` 初始化后再未被写过 | `index.html:304/343/371/399` |
| D2 | chip 的 `×` 只认 `'star'`，其他类型删不掉 | `removeFilterChip()` |
| D3 | 「重置」亮灭判断含标签/题型/状态，但无 chip 可视化 | `renderInlineActiveChips()` 的 `hasActive` |
| D4 | 题型/难度浮层的 checkbox 是静态 HTML，**重新打开浮层勾选状态全丢**（filterState 还在，视觉上却像没选） | `dropdownType` / `dropdownStar` |
| D5 | `toggleTypeFilter` / `toggleStarFilter` 用 `push` **不去重**，重复勾选会往数组里塞重复值 | `index.html:4477/4483` |
| D6 | 标签浮层 placeholder 写着「按住 Alt 点击设为排除」，`handleTagFilterClick` **根本没处理 Alt**，`excludedTagIds` 永远是空 | `index.html:4538` |
| D7 | 标签浮层按钮**不显示选中态**，选没选中视觉上分不出 | `renderTagFilterDropdownList()` |
| D8 | 难度星级只有**精确匹配**（勾 5★ = 只看恰好 5 星），没有「5★及以上」 | `index.html:2359` |

## 3. 设计

### 3.1 两层呈现

**第一层 · 胶囊计数徽标**（哪个维度被动过）

每个胶囊内加一枚计数徽标，跟该维度 chip 同色；未选中的维度不挂徽标，胶囊保持素色。

| 胶囊 | 徽标计数 | 颜色 |
|---|---|---|
| 🏷️ 标签筛选 | `selectedTagIds.length + excludedTagIds.length` | emerald |
| 📑 题型 | `questionTypes.length` | sky |
| ⭐ 难度星级 | `difficultyStars.length + (difficultyMinStar ? 1 : 0)` | amber |
| ✓ 解析与答案 | `(hasAnswer?1:0) + (hasAnalysis?1:0)` | teal |

**第二层 · chip 行（动了什么具体值）** — 独占第二行，位于胶囊行下方。

按维度分组，组头带维度色，值为可撤销 chip：

| 维度 | 组头 | 值 chip | × 行为 |
|---|---|---|---|
| 标签 | `🏷 标签·全含` / `🏷 标签·任一`（AND/OR 必须显性化） | `🏷 勾股定理` | 删这一个标签；组头 × 清空全部标签条件 |
| 标签排除 | 并入标签组 | `🏷 −一元一次不等式`（rose 色） | 删这一个排除 |
| 题型 | `📑 题型` | `📑 解答题` | 删这一个题型 |
| 难度 | `⭐ 难度` | `⭐ 恰好5★` / `⭐ 5★及以上` | 删这一个星级条件 |
| 解析答案 | `✓ 完整度` | `✓ 有答案` / `✓ 有解析` | 删这一条 |

折叠规则：同维度值超过 3 个 → 显示前 2 个 + `+N`，点 `+N` 原地展开（展开状态存 `expandedFilterGroups`）。

chip 行整体为空时整行隐藏（`empty:hidden`），只留胶囊行。

### 3.2 难度星级：精确 + 及以上

- `filterState.difficultyStars[]`（精确多选，保留原语义）
- `filterState.difficultyMinStar`（number | null，N★及以上）
- **互斥**：选「及以上 N」清空精确多选；勾精确星级清空及以上
- 浮层顶部新增「最低星级」按钮组 1-5，再点一次取消
- chip 文案区分：`恰好5★` vs `5★及以上`
- 过滤逻辑：`(q.difficultyStars||0) >= difficultyMinStar`

### 3.3 浮层勾选状态回显

题型/难度浮层改为**动态渲染**，按 `filterState` 重建勾选态；标签浮层按钮按 `selectedTagIds`（emerald 实心）/ `excludedTagIds`（rose 划线）渲染选中态，并实现 Alt 排除。

**实现中的额外决策**：题型 / 难度 / 解析与答案三个浮层的原生 `<input type="checkbox">` **全部换成 `<button>` 药丸**。

原因有两条：
1. 原生 checkbox 的 click 不稳定触发 `onchange`（自动化/部分输入路径只派发 click、不执行 checkbox 的默认行为，于是 change 永远不触发 —— 表现为「点了完全没反应」，console 干净但状态不变）。`<button onclick>` 是唯一可靠路径。
2. 原生小方框在窄栏里也难点中；药丸按钮样式统一，可读性更好，也更 Eagle。

选中态用 `bg-emerald-500 / bg-amber-500 / bg-teal-500` 实心 + 白字表达，与该维度 chip 同色。

## 4. 数据流

`filterState` → `applyFilters()`（过滤）→ `renderInlineActiveChips()`（chip 行）+ `updateFilterPillBadges()`（胶囊徽标）→ 两者都调用 `applyFilters()`。

智能视图存/取（`saveCurrentAsSmartFolder` / 载入）同步带上 `difficultyMinStar`。

## 5. 验收

### 5.1 自动化验收（Selenium + 系统 Edge，1600×900 真实点击）

脚本 `_e2e_filterchips.py`，**39/39 PASS**：

- 勾标签 → chip 行出现「🏷 勾股定理」，组头显示「🏷 标签·全含」，胶囊徽标 = 1 → 2
- 浮层内标签显示选中态（实心绿）
- Alt 点击 → 进入排除态，chip 显示「🏷 −一元一次不等式」
- 勾题型 → chip「📑 解答题」，题型胶囊徽标 = 1
- **重开题型浮层，勾选态仍在**（D4 已修）
- 难度浮层有「最低星级」5 键区；精确 → chip「⭐ 恰好5★」
- 切「5★及以上」→ chip「⭐ 5★及以上」，**精确 chip 自动消失**（互斥生效）
- 勾「必须有参考答案」→ chip「✓ 有答案」，完整度徽标 = 1
- 四个维度组头齐全；每个值 chip 有 ×；点 × 删单个条件
- 每个维度组有组头 ×；点组头 × 清空整个维度
- 一键重置 → chip 行清空 + 4 个徽标全部隐藏
- 勾 5 个标签 → 折叠成「+2」，点 +N 展开全部
- 浏览器 console 无严重错误

### 5.2 静态校验

- 回归 14/14 PASS
- `node --check` exit 0
- CRLF 计数 0
- 无调试残留（DBG log / 临时收起右栏 / 旧 checkbox 签名）

## 6. 不做

- 不引入批量勾选 / 批量落库（用户硬规则）
- 不新增独立「生成建议」按钮
- 不动真库，写操作只在副本库 + 8769

## 7. 已知遗留（不在本次范围）

- 右侧「题目属性」面板 `aside` 的 id（`rightInspectorPane` / `btnToggleInspector`）与 JS 引用不匹配 → **该面板无法收起**，窄窗口下会把中栏挤到 ~96px。属独立缺陷，本次未动。

