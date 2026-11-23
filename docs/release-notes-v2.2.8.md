## 题有据 2.2.8 更新说明

### 更新亮点

1. **AI 助手全面接管录题、自主探查与双轨切题（Agent-First Intake）**：
   - 新增 `--scan-only`（`scan_only=1`）仅扫描上传模式：MinerU 扫描无需配置看图读题密钥，本地 PDF/DOCX 与 MinerU 仅提取原页图、版面块（`Block`）与结构建议后即停在 `READY`，跳过传统自动切题与读题队列；
   - 新增原页坐标网格接口 `/api/papers/<paper_id>/pages/<page>/preview?grid=1`（内存绘制 `0–1000` 归一化网格，不改动磁盘缓存）及版面块查询接口 `/api/papers/<paper_id>/blocks`（含表格 Markdown 转换）；
   - 新增助手批量切题引擎 `/api/papers/<paper_id>/agent-cut`：支持**直录轨**（传入 `stem` 直接落库为待核对文字题，跳过内置 OCR）与**切图轨**（不传 `stem` 存为 `source_image` 原图题，可按需交回 `read-cut-questions` 队列），并支持按页/按组安全重切（强制隔离保护已通过、已入库或已编辑题卡）与整批原子撤销；
   - `tiyouju` CLI 与 MCP 同步新增 `pages`、`blocks`、`cut`、`layout`、`read-cuts`（`get_paper_pages`、`get_paper_blocks`、`cut_questions`、`edit_question_layout`、`read_cut_questions`）并更新配套 Skill 指南。

2. **题库工作台文件夹全量整理端口与双向同步**：
   - 后端持久化「我的文件夹」、智能文件夹、资料文件夹成员调整、排序偏好与标签「个人分组」（`library_folders` / `library_tag_groups`），并与题库工作台网页端双向自动同步；
   - 新增 `tiyouju folders` 全套子命令及 `list_folders`、`manage_folder`、`set_question_sections`、`update_paper_folder`、`manage_tag_groups` 等 MCP 工具，支持多级文件夹管理、批量归档移出、教材章节（`section`）规整与撤销。

3. **圆弧公式渲染与导出兼容性、API 测试及界面细节完善**：
   - 支持 `\wideparen` / `\overparen` 圆弧公式在前端 KaTeX 渲染与 Word 导出中的可伸展 `\overgroup` / `<m:groupChr>` 原生转换；
   - 优化设置页读题与答题模型测试、Thinking 模型空正文提示以及魔搭/多账号密钥填写提示与筛选栏渲染兼容性。
