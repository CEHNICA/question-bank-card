# 题有据 2.0.0 更新说明

## 本版重点更新

1. **Eagle 题库工作台与界面缩放体验**
   - 完整集成本地化 Eagle 题库工作台（`/eagle`）、A4 打印预览与导出保真（字体/列宽/跨页表头/答案双栏节奏与预览一致）、OneNote 双向回跳及统一回收站管理。
   - 新增界面整体缩放控制（`A−` / `100%` / `A+`，记忆上次比例）与阅读字号协调优化。

2. **统一答案解析编辑器**
   - Eagle 工作台单题解析编辑统一桥接至 `LibraryAnswerEditor`（`eagle-solution-editor.js` + `library-answer-editor.css`），精简顶栏与按钮文案，降低交互割裂。

3. **题库检索索引与分面统计提速**
   - 新增数据库迁移 `0033_publishedquestion_browse_indexes`，为 `PublishedQuestion` 和 `LibraryTagAssignment` 建立 6 个高频复合索引（升级首次启动自动应用，不触碰既有题库内容）。
   - 优化 `library_browse.py`：标签交集/并集/排除筛选改用 `Exists` 子查询消除多重连接膨胀，`facet_counts` 与 `dimension_presence` 改用 SQL 聚合与基础查询复用。

4. **流水线与切题引擎空值安全收口**
   - 修复 `pipeline.py` 与 `segment.py` 多处 `None` 未判空下标/属性访问隐患，并将 `core.pipeline`、`core.segment`、`core.library_browse` 正式纳入 `mypy` 静态类型门禁。
