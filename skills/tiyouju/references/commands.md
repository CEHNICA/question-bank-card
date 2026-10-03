# tiyouju 命令手册

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
| `start [--timeout 90]` | 打开题有据，等它就绪 |
| `papers` | 列出试卷：编号、名字、状态、题数、通过数、入库数 |
| `upload 文件… [--book] [--parse-mode auto\|manual\|native\|mineru] [--allow-cloud] [--wait] [--timeout 秒]` | 1.11.6 默认 `auto`，先本机处理文字 PDF，不能切出的题保留原页供框题。只有 `--allow-cloud` 才允许自动使用已配置 MinerU；失败仍保留本地结果。`manual`、`native` 可明确限定本地处理，`mineru` 明确选择云解析。`--book` 表示书或讲义。上传过的文件不会重复处理 |
| `wait <试卷> [--timeout 秒]` | 等试卷读完，打印进度。读完后告诉你有几道需要逐题核对 |
| `cards <试卷> [--filter F]` | 列出题卡和疑点。F 可以是 `todo`（需逐题核对）、`green`（识读完成、未通过）、`approved`、`ai`（AI 通过）、`human`（人工通过）、`all` |
| `show <试卷> <题号> [--out 文件夹] [--no-images]` | 一道题的全部信息，原卷截图、候选图编号截图、配图都存成 PNG |
| `fix <试卷> <题号> [--stem 文字 \| --stem-file 文件] [--option A=内容]… [--clear-option E]… [--type T] [--origin 题源] [--answer 文字] [--analysis 文字] [--force]` | 改字。没写的部分保持原样。`--stem -` 从标准输入读。只写 `--type` 时只改题型（其余疑点不动） |
| `figures <试卷> <题号> (--use 1[:slot],… \| --keep \| --none) [--force]` | 配图。slot 是 `stem` 或 `A`–`E`，不写就是 `stem` |
| `approve <试卷> <题号>…` / `approve <试卷> --green` | 打勾，记为 AI 通过。`--green` 一起通过识读完成的绿卡；仍须逐题对照原卷 |
| `unapprove <试卷> <题号>` | 撤销 AI 打的勾。人工通过的不能撤 |
| `reread <试卷> <题号> [--force]` | 让软件的读题模型重读一道题 |
| `publish <试卷>` | 把通过的题入库，并列出还没通过、没入库的题 |
| `library [关键词…] [--paper P] [--type T] [--review human\|ai] [--answer yes\|no] [--tag 知识点] [--limit N]` | 在正式题库里搜题。关键词也能搜题源和知识点 |
| `features [--enable knowledge_tags ai_answer] [--disable ...]` | 不加选项只读查看开关；明确要求生成后仅打开所需项，不自动发起模型请求 |
| `enrich tasks [--ids 入库UUID…] [--limit 1–50]` | 只读列出待当前助手处理的任务，默认最多50个；不开功能、不调用云API |
| `enrich auto [--tags on\|off] [--answer on\|off]` | 默认只读；明确设置新题入库后自动排队，只改 `on_intake` 的指定项，不开功能、不改API、不扫旧题 |
| `enrich prepare 入库UUID [--kinds tags answer] [--out 目录] [--no-images]` | 准备指定任务，返回题面、目录、prompt和指纹；默认下载该题原卷截图与配图，整页原卷返回本机URL |
| `enrich submit 任务UUID --fingerprint 指纹 --result-file 结果.json` | 一次提交一种结果：`tags`，或`answer`与可选`analysis`；只写AI附加内容，不改原卷或人工核对 |
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

先检查安装版 `tiyouju enrich --help`；旧发布包可能没有这些命令。默认让当前豆包工作版或当前助手自己完成，不需要豆包 API。用户明确要求为指定题目生成后，才启用所需开关；不趁安装或升级批量处理全部历史题。

功能开关、生成时机与“由谁生成”集中在“设置 → 标签与答案”（`/settings#ai`），直接显示在主设置页。三种触发是新题录入并入库后自动排队、后期单题、勾选批量。自动排队默认 `on_intake:{tags:false,answer:false}`；只有明确要求以后自动处理才配置，例如 `tiyouju enrich auto --tags on`。不加参数只读；指定一项会保留另一项、模型模式、API和功能开关。助手模式仍由当前助手继续领取待办，不代表豆包已在后台常驻。

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

`tags.json` 是 `{"tags":["知识点目录中的标签"]}`，选1–3个。`answer.json` 是 `{"answer":"参考答案","analysis":"参考解析"}`，解析可省略。UTF-8文件最多256 KiB；不允许夹带题面、密钥、审核或任务身份字段。也可用 `--tags 标签1 标签2`，或 `--answer-file answer.txt [--analysis-file analysis.txt]`；任务ID、指纹和助手名称始终由命令参数提供。

- `tasks` 返回 `tasks`、`total`、`limit`、`mode`、`message`。每项含任务 `id`、`publication_id`、`kind`、`executor`、`status`、`fingerprint`、`agent`、`enabled`、`stale`。关闭功能的任务仍可列出，但不能准备或提交。
- `prepare` 返回 `publication`（完整题面与入库版本）、每种任务一项的 `jobs`（`id`、`kind`、`fingerprint`、`prompt`）、`knowledge.points`（`point`、`chapter`）、`images` 和 `local_images`。CLI只下载同一道题的本机原卷截图和配图，原卷整页URL供需要时打开；MCP直接附截图与配图。
- 一次 `submit` 只交一个任务的标签或答案；必须用它自己的指纹。`complete` 返回 `job.status=done` 与当前 `publication` 后才报告成功。过期版本、配图变化、关闭开关、已完成任务或目录外标签会拒绝；重新准备并核对，不强行覆盖。
- 内容保存为 AI 附加信息，记录真实助手来源，参考答案始终“未核对”。不使用 `fix --answer` 写入原卷答案，不因此打勾或撤销人工审核。
- 整批按用户指定的入库UUID领取，每个题目分别准备，每种任务分别提交。完成当前最多50项后再取下一批；不能把批量题目拼成一个任务结果。新题任务在成功入库后才创建，识读开始前不会有生成答案。

独立模型 API 是另一条可选途径，可推荐 DeepSeek，也允许其他服务。实际模型名按服务核实或由用户填写，不虚构“DeepSeek Pro”官方 ID，不自动调用收费测试或 OCR 读题模型。

## MCP 工具

`tiyouju mcp` 提供这些工具：

- `status`、`start_app`、`list_papers`
- `configure_reading(reader, checker, minimax_plan)`：同 `config`，不传参数只看现状
- `upload_paper(paths, book, parse_mode="auto"|"manual"|"native"|"mineru", allow_cloud=false)`、`wait_paper(paper, timeout≤600)`；默认自动准备，先尝试本机文字 PDF，未切出的内容保留原卷供手工框题。只有显式 `allow_cloud:true` 才允许自动使用已配置的 MinerU；明确选择 `mineru` 表示云解析。
- `list_cards(paper, filter)`、`show_card(paper, card)`：返回文字，以及原卷截图（有候选图时是编号截图）和配图
- `fix_card(paper, card, stem, options, type, answer, analysis)`：`options` 里值为空字符串，表示删掉这个选项
- `set_figures(paper, card, use | keep | none)`
- `approve_cards(paper, cards | green)`、`unapprove_card(paper, card)`、`reread_card(paper, card)`
- `publish_paper(paper)`、`search_library(keywords, review, limit)`
- `configure_features(enable, disable)`：不传选项只看开关；按明确生成需求启用所需的 `knowledge_tags`、`ai_answer`
- `configure_enrichment_auto(tags, answer)`：不传只看现状；明确设置新题入库后自动排队，不暗自开功能、改模型或处理旧题
- `list_enrichment_tasks(ids, limit≤50)`、`prepare_enrichment(publication_id, kinds, agent)`：准备后当前助手自己看图、选标签或解题
- `submit_enrichment(job_id, fingerprint, agent, tags | answer, analysis)`：只交一种任务的AI附加结果

AI 的名字默认取 MCP 客户端的名字。
