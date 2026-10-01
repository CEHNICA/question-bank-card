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
| `status` | 题有据是否在运行、版本、密钥是否填好、能不能上传、有多少试卷。缺密钥时列出缺哪个、去哪里免费申请 |
| `config [--reader R] [--checker C] [--minimax-plan P]` | 看或改读题方式（不碰密钥，改之前先问使用者）。R：`assistant`（AI 助手读题，只要 MinerU）、`modelscope`（免费）、`minimax`、`siliconflow`。C：`auto` 或同上的服务名。P：`auto`、`plus`、`max`、`ultra`、`payg`。不加参数只显示现状 |
| `start [--timeout 90]` | 打开题有据，等它就绪 |
| `papers` | 列出试卷：编号、名字、状态、题数、通过数、入库数 |
| `upload 文件… [--book] [--wait] [--timeout 秒]` | 上传 PDF、Word，或几张照片（合成一份）。`--book` 表示书或讲义。上传过的文件不会重复处理 |
| `wait <试卷> [--timeout 秒]` | 等试卷读完，打印进度。读完后告诉你有几道需要逐题核对 |
| `cards <试卷> [--filter F]` | 列出题卡和疑点。F 可以是 `todo`（需逐题核对）、`green`（识读一致、未通过）、`approved`、`ai`（AI 通过）、`human`（人工通过）、`all` |
| `show <试卷> <题号> [--out 文件夹] [--no-images]` | 一道题的全部信息，原卷截图、候选图编号截图、配图都存成 PNG |
| `fix <试卷> <题号> [--stem 文字 \| --stem-file 文件] [--option A=内容]… [--clear-option E]… [--type T] [--origin 题源] [--answer 文字] [--analysis 文字] [--force]` | 改字。没写的部分保持原样。`--stem -` 从标准输入读。只写 `--type` 时只改题型（其余疑点不动） |
| `figures <试卷> <题号> (--use 1[:slot],… \| --keep \| --none) [--force]` | 配图。slot 是 `stem` 或 `A`–`E`，不写就是 `stem` |
| `approve <试卷> <题号>…` / `approve <试卷> --green` | 打勾，记为 AI 通过。`--green` 一起通过识读一致的绿卡 |
| `unapprove <试卷> <题号>` | 撤销 AI 打的勾。人工通过的不能撤 |
| `reread <试卷> <题号> [--force]` | 让软件的读题模型重读一道题 |
| `publish <试卷>` | 把通过的题入库，并列出还没通过、没入库的题 |
| `library [关键词…] [--paper P] [--type T] [--review human\|ai] [--answer yes\|no] [--tag 知识点] [--limit N]` | 在正式题库里搜题。关键词也能搜题源和知识点 |
| `mcp` | 作为 stdio MCP 服务器运行 |

## JSON 里的字段

`cards --json` 的每张题卡，以及 `show --json` 的开头部分：

| 字段 | 含义 |
| --- | --- |
| `id` | 题卡编号（写成 `#id` 来指定题卡） |
| `number` / `group` | 题号 / 所在分组（书里的章节） |
| `type` | `single_choice` 单选、`multiple_choice` 多选、`fill_blank` 填空、`true_false` 判断、`free_response` 解答、`unknown` 题型未定 |
| `type_blocked` | 题型还没定：这时不能通过、不能入库，先 `fix --type` |
| `origin` | 题源：题干前印的出处，单独存放，不算题干 |
| `state` | `waiting` 等待识读、`reading` 识读中、`green` 识读一致、`yellow` 需核对、`red` 识读失败 |
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

## MCP 工具

`tiyouju mcp` 提供这些工具：

- `status`、`start_app`、`list_papers`
- `configure_reading(reader, checker, minimax_plan)`：同 `config`，不传参数只看现状
- `upload_paper(paths, book)`、`wait_paper(paper, timeout≤600)`
- `list_cards(paper, filter)`、`show_card(paper, card)`：返回文字，以及原卷截图（有候选图时是编号截图）和配图
- `fix_card(paper, card, stem, options, type, answer, analysis)`：`options` 里值为空字符串，表示删掉这个选项
- `set_figures(paper, card, use | keep | none)`
- `approve_cards(paper, cards | green)`、`unapprove_card(paper, card)`、`reread_card(paper, card)`
- `publish_paper(paper)`、`search_library(keywords, review, limit)`

AI 的名字默认取 MCP 客户端的名字。
