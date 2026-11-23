## 题有据 2.2.9 更新说明

### 更新亮点

1. **正式题库解析版本层（`LibrarySolution`）批量导入、干跑预览与整批撤销**：
   - 新增 `/api/library/solutions/batch`、`GET /api/library/solutions/batches` 与 `POST /api/library/solutions/undo` 接口，以及 `tiyouju solutions <import|batches|undo>` CLI 与 `import_solutions`、`list_solution_batches`、`undo_solutions` MCP 工具；
   - 支持将整本标准答案书/官方解析批量直写正式题库解析层（支持 UUID 与 `Q-xxxxxxxx` 短码，单批最多 500 条），**不动源题卡、不清通过勾、不打回黄卡**，默认跳过已有人工解析；
   - 支持 `--dry-run` 零写入预览、未配对 `$` 公式拦截、单选/多选/填空/判断题漏填 `answer` 告警，以及按 `batch_id` 整批撤销（自动跳过后续已被老师手工修改的题目）。

2. **助手工作台真实总数、按助手汇总、批量清理与纯答案/解析直写**：
   - 修复工作台 `pending.text` 被截断为 `50` 的问题，返回并显示全库真实待核对总数、展示条数与按助手汇总；
   - 仅修改 `answer` / `analysis` 的提案在单条或批量采纳时直接写入 `LibrarySolution`，**不再将源题卡打回黄卡待核对**；题面改动提案继续严格逐题核对，严禁批量采纳；
   - 支持工作台顶部一键批量采纳纯答案/解析提案、全部清空及按指定助手清空，并支持 `tiyouju workbench --clear --from-agent <名称>`。

3. **统一「有答案」统计与筛选口径并优化列表加载性能**：
   - 绑定了有效 `LibrarySolution` 的入库题目在题库接口、`answer_inventory`、缺答案待办统计及 Eagle「✓ 有答案」筛选中统一算作「有答案」；
   - `publications_json` 改为单次 SQL 批量预取整页 `LibrarySolution`，消除列表加载 N+1 查询。

4. **免费视觉与答题服务池扩展、连接失效提示与解析排版优化**：
   - 新增英伟达 NIM、Google Gemini、智谱 BigModel 免费视觉与解题通道及 OpenRouter 免费池支持，完善后台任务队列与多账号池调度；
   - 当连接校验失效时在工作台与设置页显式提示 `unverified_reason`，并在 Eagle 卡片、快速审题与 `QB.tidyText` 中保留教辅多栏目/分段解析换行。
