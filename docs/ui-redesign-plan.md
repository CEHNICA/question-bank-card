# 题有据 · 界面做到最好 · 实施计划

> 目标：不换技术栈、不重写业务逻辑，把题有据的界面从「能用的手搓 CSS」做成「有规范、可维护、可回归、可交付」的产品级界面。
> 版本基线：main 分支当前源码（1.27.0 开发线）。
> 本文件只是计划，尚未改动任何产品代码。

---

## 0. 验收标准（先定尺子，避免"看着变好"）

| # | 指标 | 当前 | 目标 | 怎么量 |
|---|---|---|---|---|
| A1 | 颜色字面量 | **608 处 / 375 个不同色值 / 46 个令牌（覆盖率 <12%）** | ≤ 40（全部落进语义令牌） | `tools/check_css_tokens.py` |
| A2 | 令牌条目数 | 46 | ≥ 120（颜色/间距/字阶/圆角/阴影/层级/控件高度/动效） | `design-tokens.css` 计数 |
| A3 | 字号档位数 | **36 个不同值**（半档离群 12.5/13.5/11.5/10.5/14.5/15.5 共 117 处） | ≤ 6（11/12/13/15/17/20） | 令牌化后统计 |
| A4 | 圆角/阴影写法 | 圆角 15 个数值 / 阴影 **67 种写法**（66 处裸写） | 圆角 ≤ 5 档、阴影 ≤ 6 条令牌 | 令牌化后统计 |
| A5 | z-index 刻度 | **67 处 / 31 个数值，无刻度**；`z-index:60` 被 4 个互斥组件共用 | 固定 8 档（`--z-*`） | 令牌化后统计 |
| A6 | 页面级视觉基线 | 无 | 覆盖 6 个关键界面 × 3 个宽度 | `tools/shot_ui_baseline.py` 产出 PNG 入库 |
| A7 | 改动回归 | 靠人工看 | 截图自动生成 + 关键断言自动跑 | Playwright 脚本 + 现有 130 个前端测试全绿 |
| A8 | 对比度合规 | 未测 | 正文 ≥ 4.5:1，次要文字 ≥ 4.5:1，控件描边 ≥ 3:1 | 对比度计算脚本 |
| A9 | 键盘可达 | 部分 | 全部交互控件有可见焦点环、Tab 顺序合理 | 焦点巡检脚本 + 人工 |
| A10 | 文档 | 无 | 一份《界面规范》+ 一页组件速查 | `docs/ui-system.md` + `frontend/showcase.html` |

**A1–A5 是"地基"，A6–A7 是"闭环"，A8–A10 是"交付"。** 地基没做完不要开始美化任何一屏。

---

## 1. 现状盘点（实测事实，带证据）

### 1.1 文件规模

| 文件 | 大小 | 说明 |
|---|---|---|
| `frontend/styles.css` | 145,082 B / 1739 行 | 全站共用样式 + 令牌源（`:root` 在 6–59 行） |
| `frontend/library.css` | 143,950 B | 题库页样式 |
| `frontend/app.js` | 483,650 B | 终审页逻辑 |
| `frontend/library.js` | 429,459 B | 题库页逻辑 |
| `frontend/library-tag-manager.css` | 21,707 B | 标签管理 |
| `frontend/library-tag-map.css` | 8,517 B | 标签地图 |
| `frontend/library-reading.css` | 17,087 B | 阅读视图 |
| `frontend/library-tag-manager.js` | 65,519 B | |
| `frontend/library-tag-map.js` | 38,024 B | |

### 1.2 样式债（实测统计）

| 指标 | styles.css | library.css | tag-manager | tag-map | reading | 合计 |
|---|---|---|---|---|---|---|
| 硬编码 hex | 165 | 140 | 121 | 67 | 12 | **505** |
| 硬编码 rgba | 69 | 30 | 0 | 0 | 4 | **103** |
| `var(--…)` 使用 | 857 | 753 | 60 | 4 | 40 | **1714** |
| 规则 / 声明 | 1702 行 | 1714 行 | 226 行 | 80 行 | 236 行 | 3,858 行 / 10,058 声明 |

补充实测（只读审计）：颜色字面量共 **608 次、375 个不同色值**，根令牌只有 **46 个** → **令牌覆盖率 < 12%**。

结论：**令牌机制已经存在且被大量使用，但覆盖面只有约十分之一**——其余是散落的硬编码色，且没有第二个令牌文件，语义全靠命名约定。

### 1.2.1 审计挖出的四个结构性问题（原计划低估）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| **S1** | **两套主题并存**：题库页在 `library-reading.css:4-17` 重新定义 10 个根令牌（`--bg/--muted/--faint/--accent/--line/--radius-lg`…），值与 `styles.css:6-59` 全部不同 | `library-reading.css:4-17` | 同一个 `--muted` 在题库页 4.95:1（达标）、在全局 4.13:1（不达标）——**同一应用两套调性**，正是 `styles.css:110-120` 注释里反复呼吁要根除的"两页各写各的" |
| **S2** | **令牌 + 写死 fallback 双真值**：`library-tag-manager.css` 121 个 hex **全部**是 `var(--muted, #747971)` 这种写法，fallback 值与真值不同（`#747971` vs `#7b7e76`、`#e3e3dc` vs `#e5e2d9`、`#262c29` vs `#1f2320`） | `library-tag-manager.css:9,14,33,…,225`（51 处 fallback） | **同一个 `--muted` 在标签管理页和题库页是两个颜色**，且两处对比度都不达标；这是全项目**性价比最高的机械修复** |
| **S3** | **重复选择器 275 组**（200+ 组属性冲突）；`styles.css:1401-1738` 整块打印样式把 ~60 个选择器重写一遍；`library.css:786-1100` 与 `1339-1390` 的 `.print-*` 整组重写；`library-reading.css` 35 组同选择器写两遍 | 见审计 §5.1 | 死代码占比高，且**同一元素最终取值取决于 link 加载顺序**（`library.html:10-14`：styles→library→tag-manager→tag-map→reading），`library.css` 里有一批实际是死代码 |
| **S4** | **98 个 CSS 类在 HTML/JS 中零引用**（`.tag-manager-*` 11 个、`.tag-editor-*` 13 个、`.state-*`/`seg-*` 11 个、`.grip-*` 8 个）；**5 个死令牌**（`--yellow`/`--yellow-soft`/`--variant`/`--radius-sm`/`--map-green`）；`library.css:1065` 的 `var(--ink-3)` **无 fallback → 该属性直接失效**（真 bug） | 见审计 §5.4 | 维护负担 + 1 个已知失效样式 |

### 1.3 关键约束（决定了技术路线）

| # | 约束 | 证据 | 对方案的影响 |
|---|---|---|---|
| C1 | 前端文件由 Django 显式路由提供 | `backend/qb_server/urls.py:33-35`、`views.py:762-764` | 新增前端文件必须加路由，否则 404 |
| C2 | **组卷 PDF 导出把 CSS 内联拼接** | `backend/core/library_pdf.py:383` | **禁止在 styles.css 里用跨文件 `@import`**（内联后基准 URL 不可靠 → 令牌丢失 → 排版崩） |
| C3 | 打包整目录收 frontend | `packaging/QuestionBankCard.spec:51-60` | 新增前端文件自动进包，但验收白名单要同步（`packaging/make_local_acceptance.py:59-60`） |
| C4 | 已有 130 个前端 DOM 测试 + 60 个 Playwright 脚本 | `frontend/test_*.js`、`tools/check_*_browser.py` | 任何改 DOM 结构的方案都要过这些；**优先改样式不改结构** |
| C5 | 回归脚本用隔离 fixture | `tools/check_library_ux_browser.py:20-25` 强制 `QB_DATABASE/QB_DATA_ROOT` 在 checkout/tmp 下 | 视觉基线也必须用隔离数据，不碰用户题库 |
| C6 | 本机无 Django、无 `.venv` | 实测 `import django` 失败，`backend/.venv` 不存在 | 起本地服务做视觉基线需要先建隔离 venv（装 `backend/requirements.lock.txt` 精确版本） |
| C7 | 浏览器栈已就绪 | playwright 1.58 + chromium-1208 + Edge + Chrome | 无需再装浏览器 |
| C8 | **5 处测试钉死了 CSS 字面量** | `test_spots_region_read.js:59`（`border: 2px dashed #c2410c`）、`test_review_toolbar.js:29`（`color: #fff`）、`test_edit_preview.js:32`（`z-index: 60`）、`test_library_compact.js:190`（`.library-search input:focus {...box-shadow: 0 0 0 3`）、`test_interaction_round4.js:70`（`.library-withdraw { color: var(--muted) !important; }`） | **改 CSS 前先改测试断言形式**（行为不变），再改 CSS；否则红绿混乱，无法判断是样式坏了还是断言过期 |
| C9 | 可访问性红线 | `--faint` 在 `--surface` 上 **2.44:1**、`--muted` **4.13:1**（AA 正文需 4.5:1）；焦点环 4 种颜色（`--accent` / `#318c79` / `#77b3a0` 2.40:1 / `#fff`）；**6 处主动抹掉 outline**；≤11px 字号 **89 处**（标签地图 80 行里就有 11 处） | 属"改了立刻见效且零 DOM 风险"的一批，优先做 |

### 1.4 组件词汇表（真实类名，供 P1 建组件层）

`.button`（+`primary/small/quiet/outline/danger/is-busy`/`.kbd-hint`）、`.link-button`、`.chip`（+`green/yellow/red/approved/ai-approved/published/stale/waiting/plain`）、`.field`（+`inline`）、`.card`（`.card-head/.card-body/.card-source/.card-tick/.card-origin/.card-actions`）、`.filters/.filter/.filter-count`、`.dialog/.dialog-head/.dialog-tools/.hint`、`.toast/.toast-text/.toast-action`、`.menu/.menu-panel/.menu-divider`、`.drop-zone`、`.icon-button`、`.topbar/.sidebar/.toolbar/.layout`、`.settings-*`、`.paper-item`、`.plan-field`。

---

## 2. 总体思路

**不换栈、不重写、换工作法。**

四层推进，每层都可独立验收、可随时停：

```
第 1 层  令牌层   design-tokens.css  ← 单一真相源，消灭 621 处硬编码
第 2 层  组件层   组件约定 + showcase 预览页  ← 改一处、全站一致
第 3 层  页面层   六个关键界面逐个重设计（先终审页，后题库页）
第 4 层  闭环层   截图基线 + AI 视觉评审 + 像素回归  ← 改完能证明变好了
```

关键判断：**先做令牌与闭环，再做美化**。反过来做（先改好看再补规范）会把 621 处硬编码固化成新债，且无法回归验证。

---

## 3. 阶段任务（P0 → P5）

### P0 · 环境与基线（0.5 天）

| 任务 | 动作 | 产出 | 验收 |
|---|---|---|---|
| P0-1 隔离开发环境 | `python -m venv tmp/venv`；`tmp/venv/Scripts/pip install -r backend/requirements.lock.txt` | 可跑 `backend/manage.py` 的本地环境 | `manage.py check` 通过 |
| P0-2 视觉基线脚本 | 新增 `tools/shot_ui_baseline.py`：隔离 fixture → migrate+seed 合成数据 → 起 Django（仅 web，不起 worker）→ Playwright+Edge 截 6 界面 × 3 宽度 | `tmp/ui-baseline/*.png` + 一份 `baseline.json`（视口、等待条件、失败项） | 截图非空、尺寸正确、无 pageerror |
| P0-3 静态审查工具 | 新增 `tools/check_css_tokens.py`：统计硬编码色/字号档位/圆角/阴影/z-index/`!important` | 退出码非 0 即超标（可进 CI 式自检） | 用当前代码跑出基线数字，与本文件 §1.2 一致 |
| P0-4 打包白名单同步 | `packaging/make_local_acceptance.py` 增加新文件必查项 | 打包不漏文件 | 跑一次 `make_local_acceptance.py` |

**注意**：P0 全部产物放在 `tmp/` 与 `tools/`，不碰 `frontend/` 产品代码。

---

### P1 · 令牌层：单一真相源（2–2.5 天）★ 最高杠杆

**目标**：608 处颜色字面量 → ≤ 40；令牌 46 → ≥ 120；消除 S1/S2 双真值。

**执行顺序按"风险从低到高"分三波，每波独立可交付、独立可停。**

#### Wave 0 · 零风险清账（0.5 天）——不动结构，只改值与写法

| # | 动作 | 证据 | 影响面 |
|---|---|---|---|
| P1-W0-1 | `--faint` `#a5a79e` → 提到 ≥3.5:1（`#8c8f86` 量级）；`--muted` `#7b7e76` → 提到 ≥4.5:1（`#6f736a` 量级）。**只改 `styles.css:15,16` 两个值** | 审计 §6.2 | ✅ 零 DOM 断言受影响；`test_library_tag_contrast.js` 只读 `--surface`/`--ink-2` |
| P1-W0-2 | **删掉 `library-tag-manager.css` 全部 51 处 `var(--x, #hex)` 的 fallback 第二参数**，改纯 `var(--x)` | `library-tag-manager.css:9,14,33,…,225` | ✅ 不影响（两个相关测试只查选择器存在性）；一次性消掉 121 个 hex 中的 51 个，并让 `--muted` 全站一个值 |
| P1-W0-3 | 地图页小字提一档：`library-tag-map.css` 的 10px/11px → 11px/12px，颜色换 `--muted`/`--ink-2`（现状 2.93:1 实际不可读） | `library-tag-map.css:9,11,14,16,40,42,44,47,51,52,76` | ✅ 不影响（测试只钉 `.tag-map-dot{border-radius:50%}`）；**标签地图是 1.22.0 新功能，现在改成本最低** |
| P1-W0-4 | 补 4 处被抹掉的焦点环 + 统一 `--focus-ring` 令牌（`library.css:58` 搜索框改 `:focus-visible`；`styles.css:1017/553/985/1096` 至少给 `outline: 2px solid var(--accent)`；`library-tag-map.css:7,19` 的 `#77b3a0` 换实色） | 审计 §6.1 | ⚠️ **必须先改 `test_library_compact.js:190` 的断言形式**（`:focus` → `:focus-visible`，行为不变） |
| P1-W0-5 | 修 `library.css:1065` 的 `var(--ink-3)`（**无 fallback → 颜色没生效**）：改用现有 `--ink-2` 或补定义 | `library.css:1065` | ✅ 不影响 |

> **Wave 0 交付即可见**：全应用"灰字看不清"、地图不可读、焦点消失三类问题当场消失，且不碰任何 DOM 结构、不动题库、不动导出。

#### Wave 1 · 令牌骨架与刻度（1 天）

| # | 动作 | 验收 |
|---|---|---|
| P1-W1-1 | 新建 `frontend/design-tokens.css`，按 9 组分区写：① 基础色 ② 语义色（accent/green/amber/red/blue/violet + soft 变体 + 新增 `--warn`/`--danger`/`--info`/`--on-accent`/`--accent-line`）③ 文字阶 ④ 间距阶 `--space-1..8`（2/4/6/8/12/16/24/32）⑤ 字阶 `--text-xs..2xl` ⑥ 圆角 5 档 ⑦ 阴影 6 条（含 `--ring`/`--inset-line`/`--inset-warn`）⑧ 层级 `--z-base/sticky/topbar/dropdown/drawer/overlay/modal/toast` ⑨ 控件高度/动效时长/焦点环 | 令牌 ≥ 120 |
| P1-W1-2 | **引入方式（关键决策）**：❌ 不用 `@import`（违反 C2，会打爆 PDF 导出）→ ✅ Django 路由新增 `path("design-tokens.css", views.design_tokens)`，两个 HTML 的 `<link>` 放在 `styles.css` **之前**；同时删掉 `styles.css:6-59` 的令牌定义，只留 `font-family/color/background` 等全局属性 | 令牌不重复定义、加载顺序正确 |
| P1-W1-3 | PDF 导出链路同步：`backend/core/library_pdf.py:383` 拼接改为 `["design-tokens.css","styles.css",katex,"library.css"]` | 组卷 PDF 与网页配色/间距一致（有对比截图） |
| P1-W1-4 | **消除 S1 双主题**：`library-reading.css:4-17` 的 10 个令牌覆写 → 删除，两页共用 `styles.css` 令牌；确需差异的部分显式化为 `--theme-library-*` 并写明理由 | 同一 `--muted` 全站一个值 |
| P1-W1-5 | 清死令牌（`--yellow`/`--yellow-soft`/`--variant`/`--radius-sm`/`--map-green`）与死选择器（98 个，先 grep 确认无引用） | `test_library_inspector_lean.js:57-58`、`test_library_todo_merge.js:291` 仍绿 |
| P1-W1-6 | z-index 归入 8 档刻度；`z-index:60` 的四处互斥（`.drawer-scrim`/`.lens`/`.card.editing`/`.rail-scrim`）明确归属 | ⚠️ **先改 `test_edit_preview.js:32`**（钉 `z-index: 60`）；`test_figure_interactions.js:64` 的 `2147483647` 保留为特例不动 |

#### Wave 2 · 全量归一（1 天）

| # | 动作 | 验收 |
|---|---|---|
| P1-W2-1 | 608 → ≤ 40：按语义把硬编码色映射回令牌（绿系 9 个近似色 → `--accent*`；红系 8 个 → `--red/--danger/--warn`；蓝系 4 个 → `--blue/--info`；`#fff` 反白字 → `--on-accent`）。**逐文件逐选择器替换，每批 ≤ 40 处，每批跑全部前端测试 + 像素 diff** | 硬编码 ≤ 40 |
| P1-W2-2 | 字号 36 → 6 档（`12.5→12` 56 处、`13.5→13` 19 处、`11.5→12` 34 处、`10.5→11` 3 处、`14.5→15` 3 处、`15.5→15` 2 处、`9/9.5→10` 2 处） | ⚠️ 保留 `15px`（`test_library_compact.js:267` 钉 `.qb-options{font-size:15px}`）；`test_library_context_sidebar.js:58` 钉的 `min-height:30px` 不动 |
| P1-W2-3 | 间距：gap 45 → 8 档（`6/7/8/9/10` 合计 271 次压到 `6/8/10/12`）；padding >60 → 2/4 倍数格 | ✅ 基本不影响 |
| P1-W2-4 | 圆角 15 值 → 5 档令牌 + `50%` + `999px`；阴影 67 写法 → 6 条令牌（`styles.css:418` 与 `library.css:71` 逐字重复的那条合并为 `--shadow-sm`） | ⚠️ 保留 5 处 `border-radius:50%`（`test_library_tag_color.js:160-165`）、`.to-top` 的 50%（`test_library_locate_and_search.js:115`） |
| P1-W2-5 | 重复选择器 275 组：打印块与 `.print-*`、`.tag-picker-*`/`.tag-manager-*` 整组去重（**只合并同类项，不改视觉**） | ⚠️ `test_print_question_tools.js:73-74` 钉 `background:#fff` |
| P1-W2-6 | ⚠️ **先改测试再改 CSS**：`test_spots_region_read.js:59`（`#c2410c`→`var(--warn)`）、`test_review_toolbar.js:29`（`#fff`→`var(--on-accent)`）、`test_interaction_round4.js:70`（`!important` 写法→ 提高具体度） | 断言行为不变、只改写法 |

**风险与回滚**
- 每波独立提交粒度（工作区 diff，不自动 commit），失败即 `git checkout -- <文件>` 回退。
- 每波跑：130 个 `frontend/test_*.js` + 相关 `tools/check_*` + P0-2 像素 diff（容忍阈值 0.1%）。

---

### P2 · 组件层：约定 + 速查（1.5 天）

| 任务 | 动作 | 产出 |
|---|---|---|
| P2-1 组件清单 | 把现有类名整理成正式组件表：按钮 8 变体、芯片 9 变体、字段 3 态、卡片 6 区块、对话框 5 段、提示条 3 类 | `docs/ui-system.md` §组件 |
| P2-2 组件预览页 | 新增 `frontend/showcase.html`：一屏铺开全部组件 + 令牌色板 + 字阶 + 间距尺；只引用 `styles.css`，不引入任何业务 JS | 改样式时**只看这一页**就能看到全局影响 = UE 式实时预览的等价物 |
| P2-3 路由接入 | `backend/qb_server/urls.py` + `views.py` 增加 `showcase.html` 路由（照 `_frontend` 模式，`Cache-Control: no-cache`） | 顶栏「帮助」里放一个入口可进 |
| P2-4 交互状态补齐 | 每个组件补齐 hover/active/focus/disabled/loading/empty 六态（现在很多组件缺） | 组件状态表 |
| P2-5 无障碍基线 | 文字对比度达标；图标按钮补 `aria-label`；芯片/标签不只靠颜色区分（保留 ✓/AI 字样） | A8 达标 |

---

### P3 · 页面层：逐屏重设计（4–6 天）

按"改动收益 ÷ 风险"排序，一次只做一个界面，每个界面走完整闭环（设计 → 实现 → 截图 → 评审 → 修正）：

| 序 | 界面 | 路径 | 痛点（待 P0-3 审查确认） | 备注 |
|---|---|---|---|---|
| 3.1 | 终审页（逐题核对） | `/` | 卡片密度高、工具栏挤、状态色多 | 用户 90% 时间在这 |
| 3.2 | 正式题库列表 | `/library` | 侧栏/筛选/列表三层信息密度 | |
| 3.3 | 标签管理 + 标签地图 | `/library` 内 | 面板与地图两个模式切换割裂 | |
| 3.4 | 答案编辑器 | 对话框 | 长文本 + 公式 + 图片，滚动与定位易错 | |
| 3.5 | 组卷 / 打印预览 | `/library` 组卷 | A4 分页与题面排版 | 与 PDF 导出共用 |
| 3.6 | 设置（API 配置等） | `/settings` | 表单密度、开关行高不齐 | |

每屏交付标准：① 视觉规范落地 ② 令牌无新增硬编码 ③ 三宽度截图基线更新 ④ `frontend/test_*.js` + 相关 `tools/check_*` 全绿 ⑤ AI 视觉评审单闭环（评审意见全部处理或明确记为"不做"）。

---

### P4 · 闭环层：AI 视觉评审 + 像素回归（1.5 天，一次投入长期受益）

这是"做到最好"的核心机制——**让"变好了"变成可证明的事实**。

| 任务 | 动作 | 产出 |
|---|---|---|
| P4-1 截图流水线 | `tools/shot_ui_baseline.py` 输出 `tmp/ui-baseline/<界面>-<宽度>.png` + 元数据 JSON | 一条命令出全套图 |
| P4-2 像素回归 | `tools/compare_baseline.py`：与基线逐图对比，输出 diff 图与超阈值的元素清单 | 改样式不再靠肉眼 |
| P4-3 AI 评审脚本 | `tools/review_ui.py`：把截图 + 页面结构摘要送给多模态模型，按固定评审单输出结构化意见 | 每屏一份《评审单》 |
| P4-4 评审单模板 | 维度：层级与对齐、留白与密度、对比度、字号字阶一致性、状态可辨识性、焦点可见性、错误/空状态、无障碍 | 评审口径统一，不同轮次可比 |
| P4-5 模型配置 | 见 §4 | 可换模型、可并行多模型投票 |

**评审 Prompt 要点（写进 `review_ui.py`）**
```
输入：整页截图（长图，不裁切）+ 该页用到的令牌清单 + 该页的组件清单
要求：
1. 只指出"看得见"的问题，不猜代码；
2. 每条给：位置(相对坐标%)、现象、严重度(P0/P1/P2)、建议的具体 CSS 改动；
3. 明确写出"哪些地方做得好"，避免模型为了凑数而改；
4. 禁止建议引入新色相/新字体族（保持纸面暖白 + 宋体题面的既有调性）；
5. 末尾给出"本屏最值得改的 3 件事"。
```

---

### P5 · 交付与固化（1 天）

| 任务 | 动作 |
|---|---|
| P5-1 规范文档 | `docs/ui-system.md`：令牌表、组件表、间距/字阶规则、命名约定、新页面如何取用 |
| P5-2 新增页面守门 | 贡献指引：新增界面必须用令牌、必须补 showcase、必须补基线截图 |
| P5-3 一键体检 | `tools/check_ui_health.py` 汇总：硬编码色、令牌覆盖率、对比度、缺焦点环、图片 alt、层级越界 → 一行结论 |
| P5-4 打包与验收 | 同步 `packaging/make_local_acceptance.py` 白名单；跑 `packaging/make_local_acceptance.py` + `backend/manage.py test` |
| P5-5 视觉回归入库 | 基线 PNG 存 `docs/ui-baseline/`（小图可入 git，大图走 artifacts） |

---

## 4. 工具与模型配置（顶配）

### 4.1 设计侧

| 用途 | 首选 | 备选 | 说明 |
|---|---|---|---|
| 设计稿 / 规范 | **Figma**（团队协作） | Penpot（开源可自建） | 只用于"想清楚再写码"的页面；已有令牌后 Figma 变量与 `design-tokens.css` 双向对齐 |
| 快速原型交互 | **Figma Make** | Framer / Axure（复杂条件逻辑） | Axure 适合有分支的审批流式交互 |
| 组件速查落地 | **frontend/showcase.html**（P2-2） | Storybook | 本项目无构建链，showcase 是零依赖方案 |

### 4.2 实现侧

| 场景 | 选择 | 理由 |
|---|---|---|
| 现有界面整改 | **Cursor / Windsurf + 强编码模型** + 现有 `styles.css` | 保持零构建链；改动受 P0-3 静态检查约束 |
| 新组件补齐 | 强编码模型 + `docs/ui-system.md` 约束 | 组件契约先于实现 |
| 像素级微调 | 强视觉模型看截图改 CSS | 不靠猜 |

### 4.3 模型配置（按"不差 API"来配）

| 岗位 | 模型档位 | 用途 | 频次 |
|---|---|---|---|
| 视觉评审 | 顶级多模态（Claude Opus / GPT-5.x vision / Gemini Pro vision 任选，可多模型并行投票） | P4-3 逐屏评审 | 每屏 1–3 轮 |
| 代码整改 | 顶级编码模型 | 组件实现、令牌迁移 | 持续 |
| 批量微改 | 中档编码模型 | 硬编码色批量替换（P1-4） | 批量 |
| 本地兜底 | Qwen2.5-VL-7B / InternVL2.5（本地） | 离线批量粗筛、隐私数据 | 可选 |

成本控制不靠降模型，靠**只评审有变化的界面**：P0-2 的基线做像素 diff，只有 diff 超阈值的界面才送模型评审。

### 4.4 本机工具

- 浏览器与自动化：playwright 1.58（chromium-1208 已装）+ Edge（已装）
- 图像对比：`Pillow`（已装）即可做像素 diff
- 服务环境：`tmp/venv` + `backend/requirements.lock.txt`（精确版本，不动系统 Python）
- 文档与图：`docs/` + `frontend/showcase.html`；不需要额外安装

### 4.5 测试钉子清单（动 CSS 前必看）

以下 5 处 `frontend/test_*.js` 直接断言 CSS 文本字面量。**先改测试断言的写法（行为不变），再改 CSS**，否则红绿无法归因。

| 测试 | 断言的现状 | 改法 | 触发批次 |
|---|---|---|---|
| `test_spots_region_read.js:59` | `border: 2px dashed #c2410c;` | → `border: 2px dashed var(--warn);`（新增 `--warn` 令牌） | Wave 2 |
| `test_review_toolbar.js:29` | `color: #fff;` | → `color: var(--on-accent);` | Wave 2 |
| `test_edit_preview.js:32` | `.card.editing { position: fixed; inset: 0; z-index: 60; ... }` | → 断言改为 `z-index: var(--z-modal);` | Wave 1 |
| `test_library_compact.js:190` | `.library-search input:focus { ... box-shadow: 0 0 0 3` | → 随 `:focus` 改 `:focus-visible` 同步改断言 | Wave 0 |
| `test_interaction_round4.js:70` | `.library-withdraw { color: var(--muted) !important; }` | → 改为提高选择器具体度，断言同步 | Wave 2 |

**另有 4 处断言只需保持现值不动**：`test_library_compact.js:267`（`.qb-options{font-size:15px}`）、`:184`（`--topbar-h:48px`）、`test_focus_mode.js:73`（`:root.review-fullscreen{--topbar-h:0px}`）、`test_library_context_sidebar.js:58`（`min-height:30px`）。

**保留不改**：`test_figure_interactions.js:64` 的 `z-index: 2147483647`（配图槽菜单特例，语义正确）。

---

## 5. 明确不做（边界）

| 不做 | 原因 |
|---|---|
| 不引入 Electron / Tauri / Flutter / Qt | 现有 96 万行级前端全在手写 JS，重写等于重做产品；收益远低于成本 |
| 不改 `frontend/test_*.js` 已断言的 DOM 结构 | 130 个测试是行为契约；本计划以"改样式不改结构"为默认策略 |
| 不动题库数据、密钥、用户样卷 | 开发/验收只用隔离虚构数据（`tmp/` 下） |
| 不自动提交 git | 按项目规则不擅自 commit；每阶段结束给出可 review 的 diff |
| 不引入 npm 构建链 | 现有 Django 直接供静态文件，加构建链会破坏"双击图标就能用"的免安装特性 |
| 不改 PDF/Word 导出的既有排版语义 | 只同步令牌，保证导出结果与网页一致，不重排版 |

---

## 6. 里程碑与批次

| 批次 | 内容 | 产出 | 预估 | 风险 |
|---|---|---|---|---|
| **批次 A0** | P0 + **P1 Wave 0**（零风险清账） | 隔离环境、基线截图、`check_css_tokens.py`、对比度达标、51 处 fallback 清零、焦点环修复 | **1.5 天** | 极低（不改结构，只改值） |
| **批次 A1** | P1 Wave 1（令牌骨架） | `design-tokens.css` + 路由 + PDF 同步、双主题合并、z-index/阴影/圆角刻度 | 1.5 天 | 低—中（顺序敏感，逐条验） |
| **批次 A2** | P1 Wave 2（全量归一） | 608 → ≤40、字号/间距归一、重复选择器去重 | 1.5 天 | 中（需逐批像素 diff） |
| **批次 B** | P2（组件层 + showcase + 规范文档） | `showcase.html`、`docs/ui-system.md`、组件状态补齐 | 1.5 天 | 低 |
| **批次 C** | P4（闭环） | 截图/像素回归/AI 评审三件套 | 1.5 天 | 低 |
| **批次 D** | P3 逐屏重设计 | 6 个界面 | 4–6 天 | 中（每个界面单独可停） |
| **批次 E** | P5 固化 | 体检脚本、文档、验收 | 1 天 | 低 |

**总计 ≈ 11.5–13.5 天**（一个熟悉本仓库的开发者）。**批次 A0 是"当天就能看见效果"的一段**（灰字变清楚、地图变可读、搜索框焦点回来），做完再决定是否继续。每批次结束都可停、可验收、可交付。

---

## 7. 立刻可执行的第一步（命令级）

```powershell
# 1) 隔离环境（P0-1）
cd D:\question_card_bank
python -m venv tmp\venv
tmp\venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
tmp\venv\Scripts\python.exe backend\manage.py check

# 2) 现状体检（P0-3，先量后动）
#    新增 tools/check_css_tokens.py 后运行：
tmp\venv\Scripts\python.exe tools\check_css_tokens.py --report tmp\css-baseline.json

# 3) 视觉基线（P0-2）
tmp\venv\Scripts\python.exe tools\shot_ui_baseline.py --out tmp\ui-baseline

# 4) Wave 0（零风险清账，改前先改测试）
#    a) frontend/test_library_compact.js:190  断言 :focus → :focus-visible
#    b) frontend/styles.css:15,16              --muted / --faint 提到 AA
#    c) frontend/library-tag-manager.css        删 51 处 var(--x, #hex) 的 fallback
#    d) frontend/library-tag-map.css:9,11,14,…  10/11px 提档 + 换色
#    e) frontend/styles.css:1017/553/985/1096 + library.css:58 + library-tag-map.css:7,19
#                                            补焦点环，统一 --focus-ring
#    f) frontend/library.css:1065              var(--ink-3) 无 fallback 的真 bug
#    每改一项就跑：frontend/test_*.js 全量 + tmp\venv\Scripts\python.exe tools\shot_ui_baseline.py --out tmp\ui-baseline-after
```

每一步的产物都落在 `tmp/` 或新增文件里，随时 `git checkout` 回退。

---

## 8. 三个决策点（审计后已给默认答案）

| # | 决策 | 审计后的建议默认 | 备选 |
|---|---|---|---|
| 1 | 令牌引入方式 | **Django 路由 + `<link>` + 删 `styles.css` 旧定义**（避开 PDF 内联雷区 C2） | 令牌继续留在 `styles.css` 只做语义补全（省事但没有单一真相源） |
| 2 | 题库页双主题 S1 | **删除 `library-reading.css:4-17` 的 10 个令牌覆写**，两页统一（`styles.css:110-120` 注释本就要求共用骨架） | 显式化为 `--theme-library-*` 主题（保留冷灰绿调，但两套调性长期会漂） |
| 3 | 视觉基线是否入库 | **不入库**，留在 `tmp/` 由脚本随时重建（8MB × 每次改动会让 git 历史膨胀），只在 `docs/ui-baseline/` 存 6 张代表性缩略图 | 全量入库（600KB × 18 张 ≈ 11MB） |

另有一个可选项：**P2B「数据驱动界面 + 内置拖拽编辑器」**（上一轮聊的"自建 UE 时刻"）——建议**排在 P3 逐屏重设计之后**，因为先把令牌/组件/评审闭环做扎实，再让编辑器生成符合规范的面板，否则编辑器会立刻把不规范的界面固化下来。
