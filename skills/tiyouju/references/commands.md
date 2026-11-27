# tiyouju 命令手册

## 1.21.1 当前规则

本页旧版本描述由 [技能入口的当前规则](../SKILL.md) 覆盖。保存或返回审核不会提交识读；新题入库不会自动贴标。人工标签即时保存；AI/助手提交标签只生成待核对候选，任务完成不等于采纳。旧名称 CLI/MCP 仍只提交知识点候选；不要假设它支持结构化新标签或方法参数，先核实实际安装版能力。

任意层级有效标签可贴题，分组与父子关系分开；新标签候选指定同类型父级，空目录或人工明确顶级除外。方法建议依据已保存且当前采用的解析。开发、发布和验收不读取实际密钥、用户题库或样卷。

## 1.11.13：通过入库与补答案任务

题卡人工或明确授权的 AI 审核“通过”后自动保存当前版本到题库，不需另点入库；示例卷不进正式题库。publish 旧命令仍兼容，重复执行不新增版本。题库的“修改题目”可直接改题干、选项、题型和已有配图归属/删除，保存保留历史版与原卷，旧组卷不会被静默换版。

用户从答案解析窗口明确勾题补答案时，这是仅本次 solution_scope:true 任务，**无需打开全局 AI 标签/答案或自动生成开关**。使用交接文本中的同一个 job_id：enrich prepare <publication_id> --kinds answer --job-id <job_id> --agent <真实助手名> --json，实际查看返回的 local_images，逐小问解答，立即 enrich submit <job_id> --fingerprint <返回值> --result-file <UTF-8结果JSON绝对路径> --agent <真实助手名> --json。结果JSON是 answer 与 analysis 两字段，不做OCR、切题或审批。不要只领取后宣称已写回。若客户端无本机命令/MCP能力，说明限制并交付逐题解答；不要让用户反复等。

只有普通附加标签/答案任务才遵循后文先启用对应功能的规则。solution_scope 结果写任务初稿，显示在答案编辑器；用户保存后才用于出卷。取消后的任务不能重新提交或新建任务顶替，已失效题面必须重新核对。现有、指纹有效的 AI 附加答案会自动预填编辑器缺失的原卷字段；已保存人工解析优先，原卷答案保留，初稿须保存后使用。


`tiyouju.exe` 和题有据装在同一个文件夹，默认是 `%LOCALAPPDATA%\Programs\QuestionBankCard\`。它连接本机正在运行的题有据，端口从 `%LOCALAPPDATA%\QuestionBankCard\runtime\instance.json` 读，读不到就用 8768。环境变量 `TIYOUJU_URL` 可以指定别的地址。

所有命令都可以加这两个参数：

- `--json`：输出 JSON。出错时输出 `{"error": "原因", "code": 退出码}`。
- `--agent 名字`：你是谁。会显示在“××通过”上，默认是“AI 助手”。也可以设环境变量 `TIYOUJU_AGENT`。

退出码：`0` 成功，`1` 出错，`2` 题有据没打开，`3` 需要使用者处理。

`<试卷>` 可以写：试卷编号（前 8 位就够）、`latest`（最近上传的一份），或者名字里的一段。
`<题号>` 写数字，例如 `9`。一本书里题号会重复，这时用 `#题卡编号`（`cards` 会列出来）。

| 命令 | 作用 |
| --- | --- |
| `status` | 题有据是否在运行、版本、密钥是否填好、上传条件是否满足、有多少试卷。缺密钥时列出缺哪个、去哪里申请；不检测云服务当前是否可用 |
| `config [--reader R] [--checker C] [--minimax-plan P]` | 看或改读题方式（不碰密钥，改之前取得使用者选择）。R：`assistant`（AI 助手读题，处理新原卷仍需 MinerU）、`modelscope`、`minimax`、`siliconflow`。C：`auto` 或同上的服务名。P：`auto`、`plus`、`max`、`ultra`、`payg`。不加参数只显示现状；第三方额度和费用以当前账户规则为准 |
| `assistant-setup [--skill-dir 绝对skills父目录] [--replace-skill] [--desktop show\|hide]` | **1.10.14 起支持，先检查安装版 `--help`**。默认只读核实安装并列出技能/图标选择；用户明确选择后才安装配套技能或显示/隐藏本软件桌面图标。技能覆盖须获明确同意，`--replace-skill` 保留备份 |
| `setup-agents [--home 目录] [--appdata 目录] [--no-path] [--no-mcp] [--replace]` | 一键同步技能到主流国际与国内 AI Agent（含 `.workbuddy`、`.minimax`、`MiniMax Agent`、`.trae-cn`、`.qwen`、`.qoder`、`.lingma`、`.codebuddy`、`.kimi`、`.openclaw` 等），生成 `tiyouju-skill.zip`，自动注册 `tiyouju` stdio MCP 并写入用户 `PATH` |
| `mcp-config [--command-name]` | 离线输出标准 `mcpServers` JSON 配置、Codex 一行接入命令及豆包/WorkBuddy/MiniMax 导入说明，并检测本机客户端 MCP 配置状态 |
| `skill-pack [--out 路径或目录]` | 离线导出可直接拖入 **豆包电脑版 / WorkBuddy / MiniMax Agent** 的标准 `tiyouju-skill.zip` 技能压缩包 |
| `start [--timeout 90]` | 打开题有据，等它就绪 |
| `papers` | 列出试卷：编号、名字、状态、题数、通过数、入库数 |
| `upload 文件… [--book] [--parse-mode auto\|manual\|native\|mineru] [--allow-cloud] [--cut-only] [--scan-only] [--wait] [--timeout 秒]` | 默认 `auto` 本地优先。加 `--scan-only` 时仅解析原页与版面块（`blocks`），跳过自动切题与 `NEEDS_GROUPING` 停在 `READY`，便于外部助手自主切题；`--cut-only` 只切题不调用内置看图识读 |
| `wait <试卷> [--timeout 秒]` | 等试卷读完，打印进度。读完后告诉你有几道需要逐题核对 |
| `pages <试卷> [--pages 0,1 \| 0-3] [--grid] [--out 目录]` | 导出原页高清图；加 `--grid` 在导出副本上叠加 `0–1000` 坐标网格便于视觉精确定位切题坐标（不改动本机原页缓存） |
| `blocks <试卷> [--pages 0,1]` | 读取资料已提取的 MinerU / 本地文字层版面块（`seq`、`type`、`bbox`、`text`、`table_markdown`）及 `suggested_scopes` 结构 |
| `cut <试卷> (--file JSON \| --region p:x0,y0,x1,y1…) [--replace-scope none\|page\|group\|paper\|question_ids]` | 助手批量切题：传入 `stem` 走直录轨跳过内置 OCR；不传 `stem` 存为 `source_image` 原图题卡。`--replace-scope` 仅替换未审核/未入库/未编辑草稿，支持 `layout undo` 整批撤销 |
| `layout <regions\|renumber\|split\|merge\|undo> <试卷> …` | 调整已切题卡的原卷范围、改题号、拆题、合题或撤销最近一次原卷切题/布局操作 |
| `read-cuts <试卷> [题号…] [--wait]` | 将已切好但仍是原图状态（`source_image`）的题卡提交给内置看图模型队列识读 |
| `cards <试卷> [--filter F]` | 列出题卡和疑点。F 可以是 `todo`（需逐题核对）、`green`（识读完成、未通过）、`approved`、`ai`（AI 通过）、`human`（人工通过）、`all` |
| `show <试卷> <题号> [--out 文件夹] [--no-images]` | 一道题的全部信息，原卷截图、候选图编号截图、配图都存成 PNG |
| `fix <试卷> <题号> [--stem 文字 \| --stem-file 文件] [--option A=内容]… [--clear-option E]… [--type T] [--origin 题源] [--section 章节] [--answer 文字] [--analysis 文字] [--force]` | 改字。没写的部分保持原样。`--stem -` 从标准输入读。只写 `--type` 时只改题型；只写 `--section` 时只改章节目录 |
| `figures <试卷> <题号> (--use 1[:slot],… \| --keep \| --none) [--force]` | 配图。slot 是 `stem` 或 `A`–`E`，不写就是 `stem` |
| `approve <试卷> <题号>…` / `approve <试卷> --green` | 打勾，记为 AI 通过。`--green` 一起通过识读完成的绿卡；仍须逐题对照原卷 |
| `unapprove <试卷> <题号>` | 撤销 AI 打的勾。人工通过的不能撤 |
| `reread <试卷> <题号> [--force]` | 让软件的读题模型重读一道题 |
| `publish <试卷>` | 把通过的题入库，并列出还没通过、没入库的题 |
| `library [关键词…] [--paper P] [--type T] [--review human\|ai] [--answer yes\|no] [--tag 知识点] [--sort S] [--offset N] [--exclude-used-days D] [--has-analysis] [--untagged] [--limit N]` | 在正式题库里分面搜题、翻页、排除近期已用题或查缺答案/未贴标题目 |
| `stats [--paper P]` | 只读统计全库或指定试卷的题型、审核、答案、OneNote 笔记与草稿全景 |
| `locate <Q-xxxxxxxx \| UUID \| 定位文本>` | 通过短码或复制定位文本秒级调出已入库题干、答案、标签与本机原卷图 |
| `detail <Q-xxxxxxxx \| UUID \| 定位文本>` | 读取单题完整版本历史、一题多源证据、已保存答案解析与本机原卷图 |
| `tags [--kind knowledge\|method]` | 只读查看当前用户的知识点与解题方法标签树（含题目计数） |
| `dedup scan` | 只读扫描潜在重题簇、相似变式建议与已有变式题组（不自动合并或删题） |
| `variants <bind \| unbind> …` | 绑定 2 道及以上同类题为变式题组，或解除一道题的绑定 |
| `usage [--limit N]` | 只读查看近期组卷出题记录（防撞车） |
| `drafts <list \| show \| create \| update> …` | 查看或保存组卷草稿（支持传 `Q-xxxxxxxx` 与 A4/A3 参数，不提供删草稿命令） |
| `onenote list [--paper P] [--q 关键词] [--limit N]` | 只读列出已关联 OneNote 笔记的正式题目 |
| `onenote read <ref…> [--no-images] [--max-pages N]` | 严格只读提取关联 OneNote 页面的文字块与手写墨迹截图，供总结笔记和提建议 |
| `onenote open <ref…>` / `onenote create <ref…>` | 在桌面版 OneNote 打开本题笔记页，或为尚未绑定的题目新建答题页并关联 |
| `features [--enable knowledge_tags ai_answer] [--disable ...]` | 不加选项只读查看开关；明确要求生成后仅打开所需项，不自动发起模型请求 |
| `enrich tasks [--ids 入库UUID…] [--limit 1–50]` | 只读列出待当前助手处理的任务，默认最多50个；不开功能、不调用云API |
| `enrich auto [--tags on\|off] [--answer on\|off]` | 默认只读；`tags` 为兼容参数，当前版本始终不启用入库自动贴标。答案自动排队只在用户明确授权后设置，不开功能、不改 API、不扫旧题 |
| `enrich prepare 入库UUID [--kinds tags answer] [--out 目录] [--no-images]` | 准备指定任务，返回题面、目录、prompt和指纹；默认下载该题原卷截图与配图，整页原卷返回本机URL |
| `enrich submit 任务UUID --fingerprint 指纹 --result-file 结果.json` | 一次提交一种结果：旧名称知识点 `tags` 候选，或 `answer` 与可选 `analysis` 初稿；不成为正式标签，不改原卷或人工核对 |
| `claim [--limit 1–50] [--kinds tags answer] [--ids 入库UUID…]` | 领一批待处理的活。每道活带一个到期时间：助手中途没了（断网、崩了、关掉了），这道活自动回到池子里，别的助手能接着领；别人还占着、租约没到的活不会被抢走 |
| `release --job-id 任务UUID [--job-id …]` | 把没干完的活交回池子，别的助手能接着领 |
| `propose 入库UUID [--stem 文字 \| --stem-file 文件] [--option A=内容]… [--type T] [--answer 文字] [--analysis 文字] [--origin 题源]` | 给**已经入库**的题交一份**单题勘误**提案（严禁用此命令批量灌整本答案书）。题面一个字不动，等使用者在题库页点「采纳」；纯改 `answer`/`analysis` 的提案采纳时直接写入解析层不打回黄卡 |
| `solutions <import\|batches\|undo>` | **批量导入标准答案书/官方解析**到版本化解析层（不动源题卡、不清通过勾、默认不覆盖已有解析），支持 `--dry-run` 预览和整批 `undo` 撤销 |
| `workbench [--limit N] [--from-agent 助手名] [--scope all\|solution_only\|face] [--clear]` | 读助手工作台真实总数、按助手统计、谁在干活、老师此刻在哪；加 `--clear` 可批量拒绝并清空指定助手/范围的待核对提案 |
| `whereami` | 问一句：老师此刻在看哪道题、有没有没保存的改动；你刚提的改动有没有被否决 |
| `folders <list\|create\|rename\|move\|delete\|add\|remove\|sort\|reorder\|apply-tree\|custom-tree\|set-section\|paper\|tag-group\|undo>` | 直接查看和整理「我的文件夹」、资料节点位置（`paper:`/`book:` 自由归组）、书本纯数据驱动自定义目录（最多 8 层）、同级手动排序、整树 JSON 原子提交、教材章节目录（`section`）及标签个人分组，支持撤销并与前端双向同步 |
| `mcp` | 作为 stdio MCP 服务器运行 |

## 组卷导出（软件界面）

本地 1.11.4 提供 A4 分页预览、直接 PDF 与 Word 导出，**本轮没有新增 CLI/MCP 导出命令**。先核对安装版能力；助手支持界面操作时，可进入正式题库的“组卷预览”，选择题目卷、答案与解析卷或合卷，再导出 PDF 或 Word。“分别导出题目卷与答案卷”下载含两份 DOCX 的 ZIP。直接 PDF 使用本机已有 Edge 或 Chrome，不经过打印窗口；缺少可用浏览器时说明原因，不自行安装或改系统配置。

默认省纸排版，也可尽量保持单题完整；选项可全卷设置自动、一行四项或两列，再按题覆盖，单题可另起页。这些选择与字号、解答留白、姓名班级栏、输出内容一起随草稿保存和读取。实际文件生成并可读取后才报告导出完成；缺题、旧版本、缺图片或不支持的公式须说明并处理，不能漏内容后报成功。PDF 与 Word 的公式能力分别检查，不把格式失败说成所有导出都不支持。不因导出开启 AI 功能、生成缺失答案或调用云端错误解释；明确选用的 AI 参考答案保留“未核对”标记。教学只认识入口，不替用户保存真实草稿或下载文件。

## JSON 里的字段

`cards --json` 的每张题卡，以及 `show --json` 的开头部分：

| 字段 | 含义 |
| --- | --- |
| `id` | 题卡编号（写成 `#id` 来指定题卡） |
| `number` / `group` | 题号 / 所在分组（书里的章节） |
| `type` | `single_choice` 单选、`multiple_choice` 多选、`fill_blank` 填空、`true_false` 判断、`free_response` 解答、`unknown` 题型未定 |
| `type_blocked` | 题型还没定：这时不能通过、不能入库，先 `fix --type` |
| `origin` | 题源：题干前印的出处，单独存放，不算题干 |
| `state` | `waiting` 等待识读、`reading` 识读中、`green` 识读完成（不保证多次一致）、`yellow` 需核对、`red` 识读失败 |
| `approved_by` / `approval_agent` | `human`（人工通过）、`ai`（AI 通过，以及是哪个 AI）、`""`（还没通过） |
| `needs_check` | 是否需要逐题核对 |
| `issues` | 疑点，按重要程度排列 |
| `figures` / `figure_status` | 配图张数 / 配图检查结果（`blocked_missing` 可能漏图，`conflict` 配图冲突，`ok`，`confirmed_no_figure`） |
| `published` | 是否已经入库 |
| `text_source` | 文字从哪来：`mineru`（AI 助手读题时 MinerU 的初稿，还没人对照原卷核对）、`assistant`（AI 助手改过）、`human`（人改过）、空（看图读题模型读的） |
| `stem` | 题干 |

`status --json` 和 `config --json`：

| 字段 | 含义 |
| --- | --- |
| `keys` | 每家服务有没有填密钥：`mineru`、`minimax`、`modelscope`、`siliconflow` |
| `assistant_mode` | 是否是 AI 助手读题 |
| `reader` / `checker` | 实际读题、复核的服务和模型。选的那家没填密钥时，是替它读题的那家 |
| `chosen` | 设置里选的读题、复核（引擎名，`assistant` 表示 AI 助手读题） |
| `pending_change` | 刚改过的设置还没用上（从下一份新卷子开始） |
| `services` | 每家看图读题服务：`service`、`label`、`model`、`has_key`、`free`、`note`、`signup`（申请网址） |
| `missing` | 还缺什么：`mineru`（附 `signup`），或 `vision`（附免费的 `options`，以及 `or`：改用 AI 助手读题） |

`show --json` 还有这些字段：

- `options`、`answer`、`analysis`。
- `readings`：几次识读各自的写法（`a`、`b`、`c`）。
- `candidates`：候选图，每项包括 `number`（图几）、`page`（第几页）、`used_as`（用作哪里，空字符串表示没用上）。
- `images`：`crop`（原卷截图）、`candidates`（候选图编号截图）、`figures`（配图）的文件路径。

## 安装收尾的 JSON

`assistant-setup --json` 不连接题库 API，默认不修改电脑。完整收尾流程见 [install-finish.md](install-finish.md)。

| 字段 | 含义 |
| --- | --- |
| `software.installed` / `path` | 是否找到实际软件 EXE，以及其路径 |
| `software.version` / `version_verified` | 本机 EXE 的 Windows 产品版本及是否已核实，不使用源码版本代替 |
| `software.cli_path` | 与软件相邻的 CLI 路径，若存在 |
| `skill.status` / `bundled_path` / `target` | 配套技能状态、本机附带资源和目标目录 |
| `skill.backup` / `verified` | 覆盖前保留的备份与是否核实安装结果 |
| `desktop.status` / `path` / `target` / `backup` / `verified` | 本软件快捷方式的操作状态、路径、指向、备份和核实结果 |
| `desktop.requested` | 本次是否明确指定显示或隐藏；重复执行可核实已有状态，`changed` 为假时不宣称刚做了改动 |
| `questions` | 仍未选择的可选项，ID 为 `install_skill`、`desktop_icon`；结合用户已给出的选择处理，不重复询问 |
| `invitation` | 收尾时邀请用户继续发题的提示；助手应按实际能力和服务状态表达 |

默认报告中的可选设置不能代替用户同意。`--skill-dir` 必须是当前客户端已确认支持的绝对 skills 父目录；命令会安装到它下面的 `tiyouju`，不能用猜测目录。执行多项设置后按各项 `verified` 核实，不把软件安装等同于识读服务可用。

## 助手补标签与参考答案

先检查安装版 `tiyouju enrich --help`；旧发布包可能没有这些命令。本节是用户明确调用 CLI/MCP 的助手流程，由当前豆包工作版或当前助手自己完成，不需要豆包 API。用户明确要求为指定题目生成后，才启用所需开关；不趁安装或升级批量处理全部历史题。

功能开关与独立答题 API 集中在“设置 → API 配置 → 标签与答案”，位于共用配置窗口中；旧 `/settings#ai`、`#api` 链接仍打开该分区。设置外层为“API 配置、显示与导出、数据备份与迁移、帮助、关于”五区，读题密钥和模型在同一窗口的“切题与读题”中。网页使用独立 API，CLI/MCP 协议保留。标签必须主动请求单题或明确批量范围，当前 `on_intake.tags` 始终为 false，不执行 `enrich auto --tags on`；旧版自动贴标签示例已被当前规则覆盖。答案自动排队仍是兼容能力，仅在用户明确授权后设置。助手任务由当前助手继续领取，不代表豆包后台常驻。

```powershell
tiyouju features --json
tiyouju features --enable knowledge_tags ai_answer --json
tiyouju library "关键词" --json
tiyouju enrich tasks --ids <入库UUID> --json
tiyouju enrich prepare <入库UUID> --kinds tags answer --agent 豆包 --json
# 实际打开 local_images 中的原卷和配图；按 knowledge.points 选标签，自己解题并复核。
tiyouju enrich submit <标签任务UUID> --fingerprint <该任务指纹> --result-file tags.json --agent 豆包 --json
tiyouju enrich submit <答案任务UUID> --fingerprint <该任务指纹> --result-file answer.json --agent 豆包 --json
```

旧名称兼容协议的 `tags.json` 是 `{"tags":["当前目录中的知识点名称"]}`，选 1–3 个；它只保存待核对候选，不正式贴标，不能通过该协议创建新标签或提交方法范围。新标签与方法使用实际安装版支持的界面或结构化接口，不给旧 CLI 虚构 `tag_result` 参数。`answer.json` 是 `{"answer":"参考答案","analysis":"参考解析"}`，解析可省略。UTF-8 文件最多 256 KiB；不夹带题面、密钥、审核或任务身份字段。也可用 `--tags 标签1 标签2`，或 `--answer-file answer.txt [--analysis-file analysis.txt]`；任务 ID、指纹和助手名称由命令参数提供。

- `tasks` 返回 `tasks`、`total`、`limit`、`mode`、`message`。每项含任务 `id`、`publication_id`、`kind`、`executor`、`status`、`fingerprint`、`agent`、`enabled`、`stale`。关闭功能的任务仍可列出，但不能准备或提交。
- `prepare` 返回 `publication`（完整题面与入库版本）、每种任务一项的 `jobs`（`id`、`kind`、`fingerprint`、`prompt`）、`knowledge.points`（`point`、`chapter`）、`images` 和 `local_images`。CLI只下载同一道题的本机原卷截图和配图，原卷整页URL供需要时打开；MCP直接附截图与配图。
- 一次 `submit` 只交一个任务的标签候选或答案初稿；必须用它自己的指纹。`complete` 返回 `job.status=done` 与当前 `publication` 后才报告候选或初稿保存成功；不把 done 说成人工采纳或解答正确。过期版本、配图变化、关闭开关、已完成任务或目录外标签会拒绝；重新准备并核对，不强行覆盖。
- 内容保存为 AI 附加信息，记录真实助手来源，参考答案始终“未核对”。不使用 `fix --answer` 写入原卷答案，不因此打勾或撤销人工审核。
- 整批按用户指定的入库UUID领取，每个题目分别准备，每种任务分别提交。完成当前最多50项后再取下一批；不能把批量题目拼成一个任务结果。新题任务在成功入库后才创建，识读开始前不会有生成答案。

独立模型 API 是网页生成使用的途径，明确调用的 CLI/MCP 助手流程仍可独立执行。用户选择 API 后可推荐 DeepSeek，也允许其他服务。实际模型名按服务核实或由用户填写，不虚构“DeepSeek Pro”官方 ID，不自动调用收费测试或 OCR 读题模型。

## 助手和老师一起干活（领活、提案、工作台）

1.26.34 起，助手可以领活、交改法，用户在题库页「助手工作台」里点头。先检查安装版 `tiyouju --help` 里有没有这些命令。

**什么能直接改，什么不能。** 还没入库的题照旧走 `fix` 流程——那本来就是导入流程，用户会逐题核对原卷。**已经入库**的题不能直接改字：直接改会把用户打过的勾弄没，而且看不出是谁改的。已经入库的题要用 `propose` 交提案。

```powershell
tiyouju claim --limit 20 --kinds tags answer --agent 豆包 --json
tiyouju release --job-id <任务UUID> --agent 豆包 --json
tiyouju propose <入库UUID> --stem "新的题干" --agent 豆包 --json
tiyouju solutions import --file answers.json --dry-run --agent 豆包 --json
tiyouju solutions import --file answers.json --note "必修第一册" --agent 豆包 --json
tiyouju solutions batches --json
tiyouju solutions undo --batch-id <批次UUID> --json
tiyouju workbench --json
tiyouju workbench --clear --from-agent 海鸥 --reason "改走批量解析导入" --json
tiyouju whereami --json
```

- `claim` 领一批活，每道活带一个到期时间（30 分钟）。助手中途没了（断网、崩了、关掉了），这道活自动回到池子里，别的助手能接着领；别人还占着、租约没到的活不会被抢走。干不完的用 `release` 主动交回。
- `propose` 给**已经入库**的题交一份**单题**勘误提案。**题面一个字不动**，等用户在题库页「助手工作台」点「采纳」才真的改。采纳纯答案/解析提案时直接写入版本化解析层，不动源题卡和审核勾；采纳题面提案后题卡改成助手提议的样子，卡上留一条提醒，并回到「待核对」，用户对照原卷点「通过」后题库才更新。不采纳也要记下来：同一个助手再提一模一样的改法会被直接拒掉，并带回老师的理由。
- `solutions import` 用于**批量写入整本标准答案书/官方解析**（每次最多 500 条，支持 UUID 或 `Q-xxxxxxxx` 短码）。严禁用 `propose` 循环提交几百条答案提案。务必先加 `--dry-run` 预检并抽样请老师确认：单选/多选/填空/判断题要把最终结果写入 `answer` 字段、过程写入 `analysis` 字段，切勿全塞进 `analysis` 导致“仅解析无答案”。默认 `--overwrite` 关闭以保护已有人工解析；入错可随时用 `solutions undo` 整批撤销（自动跳过老师后续手工改过的题）。
- 题卡后来改过、还没重新入库，或题库里的版本已经变了，这条提案就作废，要按现在的样子重提。
- `workbench` 读助手工作台：返回全库真实待核对总数（不被截断）、按助手/类型汇总、谁在干活、采纳多少次、不采纳多少次、用户此刻在哪；加 `--clear` 可批量拒绝清空待核对提案（CLI 仅允许拒绝清理，不代老师批量采纳）。
- `whereami` 问一句：用户此刻在看哪道题、有没有没保存的改动；你刚提的改动有没有被否决。**用户正在改的题别去领、也别去提改法。**

不给助手开这些权限：删除题卡、撤回入库、改标签目录、无人值守自动通过并入库。助手交上来的东西一律先落到「待核对」，由人点头。

## 整理题库文件夹

文件夹整理（自建文件夹树、题目归档、排序、教材 `section` 章节小节、试卷/教材名称与类型、标签管理里的个人分组）通过 `/api/library/folders`、`/api/library/tag-groups` 与 `PATCH /api/papers/<id>` **直接生效并支持撤销**，不会篡改正式题面或审核状态，且会与题库工作台网页自动双向同步。

```powershell
# 1. 查看完整文件夹树（我的文件夹、试卷、教材章节树、智能夹、排序、个人分组）
tiyouju folders list --json

# 2. 我的文件夹与资料归组：新建 / 建子夹 / 重命名 / 移动层级 / 手动排序 / 删除（最多 8 层）
tiyouju folders create "专题一 · 三角函数" --json
tiyouju folders create "恒等变换" --parent "专题一 · 三角函数" --json
tiyouju folders rename "恒等变换" "三角恒等变换" --json
tiyouju folders move "三角恒等变换" --json                     # 不传 --parent 移回根目录
tiyouju folders move book:<uuid> --parent "一轮复习" --json     # 把整本教材归入自定义主题夹
tiyouju folders move book:<uuid> --parent default --json       # 恢复到默认「教材版本」分组
tiyouju folders sort section:folders manual --json             # time_desc / time_asc / name / count_desc / manual / default
tiyouju folders reorder "专题一 · 三角函数" "基础篇" "提高篇" --json
tiyouju folders delete "三角恒等变换" --json

# 2.5 书本自定义自由目录接管与整树原子提交（不改题目版本、支持 dry-run 与单步 undo）
tiyouju folders custom-tree <paper> on --json                  # 启用纯数据驱动自由目录（跳过硬编码页码表）
tiyouju folders apply-tree --file book-tree.json --root book:<uuid> --dry-run --json
tiyouju folders apply-tree --file book-tree.json --root book:<uuid> --sync-sections --json

# 3. 批量归档 / 移出题目（支持入库 UUID 或 Q-xxxxxxxx 短码）
tiyouju folders add "专题一 · 三角函数" Q-1a2b3c4d Q-5e6f7a8b --json
tiyouju folders remove "专题一 · 三角函数" Q-1a2b3c4d --json

# 4. 整理教材/教辅章节与小节目录（直接写入，不新建版本且保留原有审核，支持撤销）
tiyouju folders set-section --section "第1节 公式法 · 例题" Q-1a2b3c4d Q-5e6f7a8b --json
tiyouju folders set-section --map-file section-map.json --json

# 5. 重命名试卷/教材或切换「试卷 ↔ 教材」
tiyouju folders paper latest --name "高中数学思想方法导引" --type book --json

# 6. 整理标签管理里的个人分组（不改动知识点上下级）
tiyouju folders tag-group list --json
tiyouju folders tag-group create "高一上期末复习" --tags "集合的概念" "基本不等式" --json
tiyouju folders tag-group add-tags "高一上期末复习" "函数的概念" --json
tiyouju folders tag-group remove-tags "高一上期末复习" "集合的概念" --json
tiyouju folders tag-group undo --json

# 7. 撤销最近一次文件夹或章节整理操作
tiyouju folders undo --json
```

## MCP 工具

`tiyouju mcp` 提供这些工具：

- `status`、`start_app`、`list_papers`
- `configure_reading(reader, checker, minimax_plan)`：同 `config`，不传参数只看现状
- `upload_paper(paths, book, parse_mode="auto"|"manual"|"native"|"mineru", allow_cloud=false, cut_only=false, scan_only=false)`、`wait_paper(paper, timeout≤600)`；`scan_only=true` 仅生成原页和 `blocks` 并停在 `READY`，跳过自动切题；只有显式 `allow_cloud:true` 或 `parse_mode="mineru"` 才调用 MinerU。
- `get_paper_pages(paper, pages, grid=false)`：导出原页高清图（单次最多附加 8 张），`grid=true` 时叠加 `0–1000` 归一化网格。
- `get_paper_blocks(paper, pages)`：返回 MinerU/本地文字版面块及 `bbox`、`table_markdown` 与 `structure`。
- `cut_questions(paper, questions, replace_scope="none", ...)`：助手批量切题，双轨支持直接录入文字或保留原图；严格保护已通过/已入库/已编辑题卡，支持撤销。
- `edit_question_layout(paper, action="regions"|"renumber"|"split"|"merge"|"undo", ...)`：微调原卷范围、改号、拆合或撤销布局/批量切题操作。
- `read_cut_questions(paper, cards, wait=false)`：把原图题卡提交给软件内置看图模型进行识读。
- `list_cards(paper, filter, summary)`、`show_card(paper, card)`：返回文字，以及原卷截图（有候选图时是编号截图）和配图；`summary=true` 只返回统计摘要
- `fix_card(paper, card, stem, options, type, answer, analysis, origin, section)`：`options` 里值为空字符串，表示删掉这个选项；`section` 设置章节小节目录
- `set_figures(paper, card, use | keep | none)`
- `approve_cards(paper, cards | green)`、`unapprove_card(paper, card)`、`reread_card(paper, card)`
- `publish_paper(paper)`、`library_stats(paper)`、`search_library(keywords, paper, type, review, answer, tag, sort, offset, exclude_used_days, has_analysis, untagged, limit)`
- `locate_question(ref)`、`get_question_detail(ref)`、`list_tags(kind)`、`scan_duplicates()`、`bind_variants(question_ids)`、`unbind_variant(question_id)`
- `list_onenote_notes(paper, q, limit)`、`read_onenote_note(ref, include_images=true, max_pages=3)`、`open_onenote_note(ref)`、`create_onenote_note(ref)`：完整覆盖 OneNote 笔记只读总结与跳页/建页（不删不改原笔记）
- `list_exam_usage(limit)`、`list_drafts()`、`get_draft(draft_id)`、`save_draft(title, question_ids, draft_id, print_options)`、`create_backup(dest, papers, folders)`
- `configure_features(enable, disable)`：不传选项只看开关；按明确生成需求启用所需的 `knowledge_tags`、`ai_answer`
- `configure_enrichment_auto(tags, answer)`：不传只看现状；当前 tags 保持关闭，标签只主动请求；答案自动排队仅在明确授权后设置，不暗自开功能、改模型或处理旧题。
- `list_enrichment_tasks(ids, limit≤50)`、`prepare_enrichment(publication_id, kinds, agent)`：准备后当前助手自己看图、选标签或解题
- `submit_enrichment(job_id, fingerprint, agent, tags | answer, analysis)`：只交一种任务的知识点候选或答案初稿；候选须人工采纳，初稿须核对并保存。
- `claim_work(limit, kinds, agent, ids)`、`release_work(job_ids, agent)`：领活与交回；每道活带到期时间，别人租约没到的活抢不走
- `propose_question_change(publication_id, agent, stem, options, type, answer, analysis, origin)`：给**已经入库**的题交一份**单题**勘误提案；题面不动，等人在题库页点采纳（严禁用此工具批量灌整本答案书）
- `import_solutions(items, overwrite, dry_run, note, agent)` / `list_solution_batches(limit)` / `undo_solutions(batch_id)`：批量将标准答案书/官方解析写入解析层，不动源题卡、不清通过勾、默认不覆盖已有解析，支持先 `dry_run=true` 干跑预览及整批撤销
- `read_workbench(limit, agent, scope)`：读助手工作台（真实总数、按助手汇总、谁在干活、人此刻在哪）
- `where_is_the_teacher()`：问人此刻在看哪道题、有没有没保存的改动，以及我提的改动有没有被否决
- `list_folders()`：查看全部文件夹树（我的文件夹、根目录资料、试卷夹、教材章节树/自定义目录、智能夹、排序、标签个人分组）
- `manage_folder(action, ...)`：直接新建/重命名/移动我的文件夹或 `paper:`/`book:` 资料节点、同级手动排序(`reorder`)、切换书本自定义目录(`set_custom_tree`)、批量归档/移出题目、设置排序、撤销（最多 8 层）
- `apply_folder_tree(tree, root, mode, takeover, set_manual_sort, sync_sections, dry_run)`：一次性原子提交整棵多级目录树（支持空目录、树内挂载资料、稳定复用 ID 及单步 `manage_folder(action="undo")` 撤销）
- `set_question_sections(section, question_ids | items)`：批量整理已入库题目的教材章节/小节目录，直接生效且支持 `manage_folder(action="undo")` 撤销
- `update_paper_folder(paper, name, material_type)`：重命名试卷/教材或切换 `exam` / `book`
- `manage_tag_groups(action, ...)`：整理标签管理里的个人分组及组内标签（支持 `undo`）

AI 的名字默认取 MCP 客户端的名字。
