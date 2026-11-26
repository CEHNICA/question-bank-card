## 题有据 2.3.5 更新说明

### 更新亮点

1. **后端类型检查（mypy）与前端测试 / ESLint 全绿收口**：
   - 修复 `backend/core/library_folders.py` 中 `_mutate_reorder` 与 `add_questions` 的 `mypy` 类型报错；
   - 保留 Eagle 工作台精简搜索占位符（`搜索题干、名称、标签…`）与完整 `aria-label` 无障碍标签，并同步测试断言；
   - 保持导出辅助脚本 ESLint 检查通过。

2. **包含 2.3.4 与 2.3.3 全部功能改进**：
   - 书本纯数据驱动自定义目录接管（`custom_tree_docs`），录新书无需改代码或发版；
   - 大一统自由树（8 层深度上限）、`paper:` / `book:` 资料节点归组与同级混合手动排序，支持 `Ctrl+Z` / `undo`；
   - Agent 整树 JSON 原子提交（`tiyouju folders apply-tree` / MCP `apply_folder_tree`），支持 `--dry-run`、`--sync-sections` 与单步撤销；
   - Eagle 工作台安静收口、统一毛玻璃悬浮气泡与详情页链接图标化。

### 验证范围

- `ruff check` / `ruff format --check`、`npm run lint` 全部通过；
- 桌面与打包测试 `unittest`、Django `core` 测试套件与前端 `node --test frontend/test_*.js` 全部通过；
- PyInstaller 冻结产物与 Inno Setup 安装包经 `audit_bundle.py` 审计与 SHA-256 校验。
