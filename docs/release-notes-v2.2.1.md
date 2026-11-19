# 题有据 2.2.1 更新说明

## 修复与改进

> 本版本完整包含 `v2.2.0` 的全部更新（导出 Word 排版对齐打印预览、按试卷/书/目录文件夹打包题包分享、OneNote 回跳轮询加固），并合入及修复以下问题：

1. **根治「原卷明明有图、题卡却提示可能漏图」误报并加固启动自愈**
   - **识读前自动回填缺失候选图（`pipeline.read_questions`）**：当历史按章节重提取或手工切题遗留的题卡缺少 `figure_candidates` 而原卷范围存在 MinerU 印刷图块时，识读前先按切题范围自动回填并持久化候选框（严守不抢占他题图块、不改写人工配图决定），从源头杜绝无框识读产生的悬空键或误报缺图。
   - **自动配图归属策略升级至 v14（`figure_policy.py`）**：`resolve_automatic_figure_assignments` 仅统计命中当前候选集合的绑定角色，消除历史无候选识读遗留悬空键的误阻塞；选择题 A–D 选项槽完整且题干提示有图时，自动将唯一剩余非徽章候选图归入题干。
   - **启动自动找回存量误报与并发短事务加固（`figure_candidate_repair.py`、`run_worker.py`）**：后台工作者启动时自动扫描未归档资料中的「黄色 + `blocked_missing`」误报卡并逐卡短事务/保存点重算（无法解除误报的卡整张回滚原样保留）；同时跳过已审核通过（`approved`）或正在识读中（`ocr_pending`）的题卡，避免启动时全库锁表与状态竞态。另提供维护命令 `manage.py repair_figure_candidates`（默认只预演，加 `--apply` 写库）。
   - **适配新手教学示例卷第 2 题练习状态（`demo-paper.json`、`build_demo_fixture.py`）**：同步适配 `figure_policy` v14 规则，使新手示例卷第 2 题继续保留蓝色候选框与待补配图提示。

2. **修复按卷 / 书打包分享的两处边界缺陷（`library_transfer.py`）**
   - **已归档的正式书籍也可打包分享**：修正 `list_exportable_papers` 对 `archived=False` 的过度过滤，改为同时保留「已入库且仍有在用正式题目（`PUBLISHED`）」的已归档书籍或资料，同时继续排除回收站废卷与示例练习卷。
   - **分享题包剔除失效历史版本**：裁剪快照库时将入库记录过滤条件收紧为仅保留 `status = 'published'`，避免同题已失效的 `superseded` 历史快照混入分享包。

3. **修复 Word 导出题号段落细节（`library_export.py`）**
   - 消除原图题（`body_mode="source_image"`）、表格开头题及行间公式开头题（无题源）在题号段落末尾残留的多余制表符 `\t`，并补全题号段落统一行距设置。
