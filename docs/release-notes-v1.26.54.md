# 1.26.54 —— 修复题卡「更多」下拉菜单截断、全界面深度统一现代 Clean UI

## 这一版忙了什么

在 1.26.53 引入助手字符级 Diff、WAL 无锁并发与题库 Clean UI 之后，我们收到了两位老师的关键反馈：
1. **题库卡片点击「更多」按钮后没有任何下拉框弹出**。
2. **美化没有做到全覆盖，进入核对题目工作台、系统设置等界面时仍保留旧版样式，视觉风格割裂脱节**。

本版本针对这两个问题进行了精准根因排查与全量界面深度升级，并引入全量自动化回归测试，保证交互手感与视觉统一性。

---

## 核心改动详解

### 1. 修复题卡「更多」下拉菜单被裁切隐藏缺陷

#### 痛点排查
题库卡片底栏包含「原卷位置」、「加入试题篮」、「导出」、「更多」等操作按钮。其中「更多」按钮使用原生 `<details class="library-card-more">` 配合绝对定位弹出层 `.library-card-menu` 实现。
由于 1.26.53 在重构卡片时，在父容器 `.library-page .library-card` 上添加了 `overflow: hidden;`，导致绝对定位到卡片外部下方的下拉菜单面板被直接整体裁切，表现为「点击更多无任何下拉框展示」。此外，下方后续题卡的堆叠层级也会遮挡下拉菜单。

#### 修复方案
- 将 `.library-page .library-card` 的 `overflow: hidden` 改回 `overflow: visible`。
- 在卡片顶部元信息区 `.library-card-meta` 与底栏 `.library-card-actions` 独立应用 `13px` 圆角，在保持整卡圆角视觉一致性的同时，不裁切任何弹出层。
- 引入现代 CSS 选择器 `.library-page .library-card:has(.library-card-more[open]) { z-index: 35; position: relative; }`，在下拉菜单展开时自动提升整张卡片的层叠上下文，彻底杜绝被后续卡片遮挡。

---

### 2. 全界面现代 Clean UI 深度统一

#### 痛点排查
先前的视觉重构主要聚焦在题库页（`/library`），而老师高频使用的**核对题目工作台（`index.html`）**、**系统设置界面（`/settings`）**、**新手练习（`practice.html`）**以及**全局模态弹窗（Dialogs）**仍沿用旧版设计，按钮圆角、背景饱和度、边框对比度存在明显差异。

#### 升级内容
- **全局顶部导航条 (`.topbar`)**：统一为高透磨砂玻璃白（`backdrop-filter: blur(12px) saturate(160%)`），圆角胶囊导航标签，与题库页完全无缝衔接。
- **核对题目工作台 (`index.html`)**：
  - 试卷左侧栏（`.sidebar`）：磨砂灰白衬底、统一平滑滚动条。
  - 试卷列表项（`.paper-link`）：现代微胶囊态，选中态为清爽品牌蓝底配蓝指示条。
  - 审核进度指示条（`.review-meter`）：纤细高保真双轨进度条。
  - 审题工具栏（`.toolbar`）：冷灰卡片悬浮容器，高对比度功能按钮。
  - 核对题卡（`.card`）：纯白卡片加微柔阴影（`box-shadow: 0 4px 16px rgba(15, 23, 42, 0.05)`），与题库卡片完全统一。
  - 卡片操作菜单（`.more-menu`）：层级提升至 `z-index: 50`，支持动态防遮挡弹出。
- **系统设置界面 (`/settings`)**：
  - 分区导航（`.settings-nav`）：重构为现代 Segmented Control 分段控制器。
  - 设置分区（`.settings-section`）：重构为统一的现代冷白卡片，优化开关（`.settings-switch`）、帮助与路径向导卡片（`.help-path`）。
- **新手练习界面 (`practice.html`)**：步骤条（`.practice-steps`）、练习卡片列表及预览浮层全面现代化。
- **全局弹窗（Dialogs）**：原卷出处（`.source-dialog`）、快捷键帮助（`.keys-dialog`）、全屏看题、草稿箱等统一磨砂背板与圆角阴影。

---

## 质量与回归测试

1. **新增防回归专用测试套件**：`frontend/test_dropdown_and_clean_ui.js`，严格断言 `.library-card` 的 `overflow: visible`、菜单层级 `:has([open])` 提权规则、全界面 topbar 磨砂规则及设置页分段选择器样式。
2. **前端全量单元测试**：`npm test` 163 项测试全部 PASS（用时 3.9 秒）。
3. **后端全量回归测试**：147 项 Python 单元测试全部 PASS（用时 34 秒）。
4. **桌面应用审计与构建验证**：
   - PyInstaller 单目录应用构建无错误。
   - `tiyouju.exe --version` 输出校验一致。
   - 安全审计通过：安装包与免安装产物中零敏感数据、零冗余日志。
   - Inno Setup 编译生成单文件安装程序 `TiYouJu-Setup-1.26.54.exe` 并完成 SHA256 校验。
