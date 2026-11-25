## 题有据 2.3.4 更新说明

### 更新亮点

1. **书本纯数据驱动自由目录接管（以后录新书不再改代码、不改软件版本）**：
   - 新增书本「自定义自由目录」接管开关（右键「更多 → 启用自定义自由目录」，或 CLI `tiyouju folders custom-tree <paper> on`）；
   - 开启后彻底跳过前端硬编码页码表、` · ` 拆两层与题号猜章逻辑，直接按数据渲染任意结构与空目录；老书默认保持原样兼容。

2. **大一统自由树：8 层深度 + 书/卷节点自由归组与混合拖排**：
   - 文件夹层级上限从 4 层放宽至 **8 层**；
   - 试卷与教材夹不再锁死在默认分组，支持拖拽或右键「移动到...」放入任意主题文件夹或文件夹根目录，并与普通文件夹同级混合手动排序（`manual` / `sortOrder`）；
   - 删除包含资料的文件夹不会删除试卷或题目，仅安全释放回默认分组，且随时支持 `Ctrl+Z` / `undo` 恢复。

3. **Agent 整树 JSON 原子提交与单步回滚（CLI & MCP）**：
   - 新增 `tiyouju folders apply-tree` 与 MCP `apply_folder_tree`：支持 `--dry-run` 干跑预览、`replace` / `merge` 模式、稳定复用已有文件夹 ID、树内挂载资料、`--sync-sections` 同步章节字段（不产生新题目版本、不掉审核勾），以及单步 `undo` 原子撤回；
   - 同步开放 `tiyouju folders reorder`、`folders move book:<id> / paper:<id>`、`folders sort ... manual`。

4. **包含 2.3.3 全部工作台界面改进**：
   - 查虫中心去遮罩、考题属性可折叠、AI 建议安静化、顶栏搜索收口；
   - 统一毛玻璃悬浮气泡、文件夹拖排撤销与展开自动滚入视野；
   - 详情页参考链接改为图标直开。

### 验证范围

- 后端 `core.test_library_folders`、`core.test_tiyouju_cli`、`core.test_app_version` 全部通过；
- 前端文件夹交互/排序/拖排/撤销/统一树测试套件全部通过，PyInstaller 冻结产物与 Inno Setup 安装包经 `audit_bundle.py` 审计与 SHA-256 校验。
