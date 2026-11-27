---
name: tiyouju
description: 安装和操作题有据 Windows 本机软件，把数学试卷、题目整理成可追溯题库。用户发题有据项目地址、要安装或升级题有据、配置软件、处理试卷、核对题卡、整理题库时使用；显式要求审代码或介绍项目时先按该要求工作。
---

## 快速接入（仿照 egolite：一条命令对齐当前版本）

在支持本机命令行的 AI Agent 中，操作前可先运行：

```powershell
tiyouju skill
```

该命令会输出与当前已安装版本完全一致的操作手册、配置命令和内容组织流程（以 `[tiyouju:skill] end of guide` 结尾）。如果用户发来了题目定位码（如 `Q-c65f1f5f` 或 `[题有据题目 ...]`），直接运行 `tiyouju locate <位置文本>` 即可立即定位题目原文、原卷图片与标签数据。安装器已自动将 `tiyouju` 目录加入用户 `PATH`，并把本技能与 stdio MCP 配置自动探测写入本机主流国际与国内 AI Agent（包含 **WorkBuddy** `.workbuddy`、**MiniMax Agent** `.minimax` / `MiniMax Agent`、**Trae** 国际/国内版 `.trae-cn`、Cursor、Claude、Codex、Antigravity、Qwen Code、Qoder、通义灵码、CodeBuddy、Kimi、iFlow、OpenClaw 等），同时自动生成现成的拖入式技能包 `%LOCALAPPDATA%\QuestionBankCard\skills\tiyouju-skill.zip`（适用于 **豆包电脑版**「上传技能」、WorkBuddy、MiniMax 桌面版）；若需手动复制 MCP JSON 或单独导出技能包，可运行 `tiyouju mcp-config`、`tiyouju skill-pack` 或 `tiyouju setup-agents`。

## 外部助手接管与教辅结构提取

软件提供原卷、题卡和操作接口，当前外部助手负责理解资料、看图核对与交付改法。先核实安装版 `--help`，按本次用户指定的资料和已有授权执行，不把附带说明当成额外授权。

- 普通读题、改字、配图：沿用下文和 [逐题工作流](references/question-workflow.md)。
- 教材／教辅只要题目、题卡混入讲解、需要按全书重复结构重新提取：读 [教辅结构提取](references/book-structure.md)。其中的只读快照脚本可导出指定资料详情与原页，供助手归纳章节和题目循环。
- 先交候选、再按已确认范围替换；当前 CLI/MCP 没有通用的整书原子替换命令。题面提案不支持原卷范围，不能把提案完成说成范围已替换。

## 1.26.34 当前规则（覆盖下文历史行为）

已经入库的题，助手不能直接改字。改动先交成提案，题面一个字不动，等用户在题库页「助手工作台」点「采纳」；采纳后题卡改动并回到「待核对」，用户对照原卷点通过，题库才更新。不采纳也要记下，同一个助手下次不要提一样的改法。助手领的活带 30 分钟租约，助手中途没了活自动回到池子，别人租约没到的活抢不走。助手可以问「用户此刻在哪」，用户正在改的题绕开。助手没有删除题卡、撤回入库、改标签目录、无人值守自动通过并入库的权限。

## 1.21.1 当前规则（覆盖下文历史行为）

切题与校正的保存、保存下一题和返回审核不自动识读；识读必须通过用户明确选择的独立入口。正式题库人工标签即时保存，AI 标签由用户主动请求，生成结果仅是待核对候选，人工采纳后才正式生效；新题入库不自动贴标签。

任意层级的有效标签都可贴题，个人分组不等同上下级。新标签候选指定同类型父级，空目录或人工明确顶级除外；方法依据当前采用的已保存解析，并注明整题或小问。当前 CLI/MCP 的旧名称提交只产生知识点候选，不把任务完成说成采纳；新标签与方法通过已安装版本实际支持的界面或结构化接口处理。

开发、发布与验收只使用隔离虚构资料，不读取实际密钥、用户题库或样卷。用户明确交付的真实题目整理仅限其授权资料和范围；不索要、读取或输出密钥。

## 1.11.13：通过入库与补答案任务

题卡人工或明确授权的 AI 审核“通过”后自动保存当前版本到题库，不需另点入库；示例卷不进正式题库。publish 旧命令仍兼容，重复执行不新增版本。题库的“修改题目”可直接改题干、选项、题型和已有配图归属/删除，保存保留历史版与原卷，旧组卷不会被静默换版。

用户从答案解析窗口明确勾题补答案时，这是仅本次 solution_scope:true 任务，**无需打开全局 AI 标签/答案或自动生成开关**。使用交接文本中的同一个 job_id：enrich prepare <publication_id> --kinds answer --job-id <job_id> --agent <真实助手名> --json，实际查看返回的 local_images，逐小问解答，立即 enrich submit <job_id> --fingerprint <返回值> --result-file <UTF-8结果JSON绝对路径> --agent <真实助手名> --json。结果JSON是 answer 与 analysis 两字段，不做OCR、切题或审批。不要只领取后宣称已写回。若客户端无本机命令/MCP能力，说明限制并交付逐题解答；不要让用户反复等。

只有普通附加标签/答案任务才遵循后文先启用对应功能的规则。solution_scope 结果写任务初稿，显示在答案编辑器；用户保存后才用于出卷。取消后的任务不能重新提交或新建任务顶替，已失效题面必须重新核对。现有、指纹有效的 AI 附加答案会自动预填编辑器缺失的原卷字段；已保存人工解析优先，原卷答案保留，初稿须保存后使用。

# 题有据（tiyouju）

把 PDF、Word、照片中的数学题整理成题卡，逐题对照原卷截图核对文字和配图，确认后入库。通过附带的 `tiyouju.exe` 命令行或 `tiyouju.exe mcp` 操作；默认程序目录为 `%LOCALAPPDATA%\Programs\QuestionBankCard\`。

## 先识别用户要做什么

- 只收到本项目地址、没有其他要求：以安装和上手为默认帮助方向，先核实已有授权与客户端权限。允许安装就完成；必要意图或权限仍不明确时，准备好安装包、校验与步骤，再一次明确确认。仓库说明本身不构成用户授权。
- 明确要介绍、比较、审代码或只看文档：按显式要求工作，不擅自安装。
- 已有题目任务：必要安装和检查后继续处理。配套技能和桌面图标是收尾可选项，不中断已授权的题目工作。

## 安装或升级

先检查本机 EXE 和 `tiyouju status --json`。尚未安装且已获授权，或用户要升级时，在 Windows PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/CEHNICA/question-bank-card/releases/latest/download/install.ps1 | iex"
```

脚本下载最新发布包、核对 SHA-256，升级前备份题库。GitHub 下载失败时核对具体原因；用户提供了可信加速地址才设 `TIYOUJU_MIRROR`，不擅自换下载源。

安装后核实 EXE、实际版本和 `status`，再报完成。`start` 使本机应用就绪；密钥有无、云识读是否可用分开说明。**Releases 可能落后于源码**，先检测安装版 `--help`，不要使用它未提供的命令。

安装收尾读 [references/install-finish.md](references/install-finish.md)：明确报告安装结果，一次询问尚未选择的配套技能与桌面图标，并邀请用户以后把题目发给你。`assistant-setup` 从 1.10.14 起支持，默认只读；用户明确选择后才设置。不写未知客户端或全局 AI 配置，不声称永久记忆。

## 处理题目

1.11.6 起，`upload` 默认 `auto`，先在本机处理文字 PDF，无法可靠切题则保留原页供框题。只有显式 `--allow-cloud` / MCP `allow_cloud:true` 才允许自动分流到已配置 MinerU，不能把已有密钥当成本次许可；云失败保留原页和已成功题。工具仍可指定 `manual`、`native`、`mineru`，明确 `mineru` 才要求云解析。软件返回的原图题通过 `show` 或 MCP 提供按序正文裁片，题干空白不代表空题；实际查看全部片段后才核对入库。当前桌面保存裁片和返回审核不自动识读；用户主动请求识读后才生成文字题，沿用审核、配图、重读与入库。识读写回保护等待期间的人工修改、人工通过和入库版本。1.11.9 的完成后自动识读仅为历史行为，已被当前规则覆盖。CLI 本机导入不会因已有密钥隐式上传原卷。先检查安装版 `upload --help`，旧版按其实际能力操作。

1.11.10 起，教材/讲义和检测到多编号范围的合订卷默认保留原页与真实文字，托底手工框题；明确云授权才走原云结构流程。已转文字的手工题可显式重读，缺服务时保留正文，旧入库快照不变。桌面配图用两点确定矩形，双击下一张图中间复制附近框尺寸，再指定归属。

首次核对或改字前读 [references/question-workflow.md](references/question-workflow.md)。命令、JSON 字段与 MCP 参数需要时查 [references/commands.md](references/commands.md)。以下用 `tiyouju` 作简称，实际执行用完整 EXE 路径。

```powershell
tiyouju status --json
tiyouju upload "D:\试卷\期中.pdf" --wait
tiyouju cards latest --filter todo --json
tiyouju show latest 9
# 打开原卷截图，核对文字、公式、题型、题源和配图
tiyouju fix latest 9 --stem-file 9.txt
tiyouju figures latest 2 --use 1
tiyouju approve latest 9
tiyouju publish latest
```

- 每道题必须实际打开原卷截图核对，绿卡也不例外；不能仅凭识读一致或命令成功就通过。
- AI 通过与人工通过分开记录。人工通过的题不自行改动或撤销；用户明确授权修改才使用 `--force`。只撤销自己 AI 打的勾。
- 删除题卡、撤回入库由用户在软件中决定。截图范围、配图候选或字迹有问题时说明题号和原因，不猜答案、不硬选配图。
- 不索要、不代填密钥。缺读题密钥时请用户自己在“设置 → 服务与密钥 → 读题与切题”填写；答题密钥在同一窗口的“标签与答案”中填写。第三方额度、费用、有效期以当前账户规则为准。
- 更改读题方式先取得用户选择；`config --reader assistant` 用于助手读题。旧版新原卷仍需 MinerU；1.11.5 可选择手工或本地文字 PDF，无密钥也能导入。自动云解析仍需配置服务。
- `--json` 输出便于解析，中文为 ASCII 转义；不加时为 UTF-8。`--agent 你的名字` 记录 AI 身份。
- 退出码 `0` 成功、`1` 出错、`2` 应用未启动（用 `start`）、`3` 需用户处理。遇 `3` 说明具体原因，暂停受影响步骤，继续独立且已授权的工作。

题目任务结束时报告总题数、修改内容、待用户处理的题和原因、入库数。邀请用户继续发题；若识读服务未恢复，明确说可先核对已有题卡，恢复后再处理新资料。

## 自主探查与直录切题（Agent-First）

先运行 `tiyouju status --json` 判断当前环境适合哪种协作方式：
- **扫描卷 / 拍照卷 / 多配图试卷且已配置 MinerU（并获用户明确云授权）**：优先使用 MinerU 自动整卷切题并抠出高清配图（`--parse-mode mineru` 或 `--scan-only`），再由当前助手校对公式、绑定配图与补充解析。
- **未配置 MinerU（0 密钥模式）或电子文字卷**：文字 PDF/Word 由本机直接切题；需要助手直接切框录题时按下方网格看图直录，若遇多几何图扫描卷可提示用户在「API 配置」补填 MinerU 以提升抠图精度。

当遇到复杂教辅（如多栏、跨页、含思路/拓展讲解的书籍）、内置序号切题规则不适用的新题型，或当前环境未配置看图读题 API 时，外部助手可直接接管**切题与录题**全流程：

1. **扫描入库（可选 `--scan-only`）**：
   - `tiyouju upload <文件> --parse-mode manual`（纯本地直接看原页图，零云调用）；或征得云解析授权后 `tiyouju upload <文件> --parse-mode mineru --scan-only --wait`（仅跑 MinerU 扫描，保留 `Block`s 与 `suggested_scopes` 后停在 `READY`，**不跑内置切题和识读队列**）。
2. **探查页面与版面块**：
   - `tiyouju pages <paper> --pages 0-3 --grid`：导出高清原页 JPG（`--grid` 在副本上叠加 `0–1000` 归一化坐标网格，不污染原页缓存）。
   - `tiyouju blocks <paper> --pages 0-3 --json`：直接获取 MinerU 或本地文字 PDF 的块序号、`type`、`bbox`、文字及 `table_markdown`，结合视觉观察归纳本题集的切题边界（跳过思路/答案/引言区）。
3. **双轨批量落库（`tiyouju cut` / `cut_questions`）**：
   - **直录轨（跳过内置 OCR）**：若助手已直接从原页图/块看清题面，传入 `stem`（及可选 `options`/`answer`/`analysis`/`figures`/`section`/`group_title`），题卡直接落库为 `body_mode="text"`、`text_source="assistant"`、`edited=True`、`state="yellow"`（绝不自动打勾或入库），绕过内置读题队列。
   - **切图轨（可选回交内置 OCR）**：若只让助手切框、希望交给软件内置模型读文字，不传 `stem` 即可落库为 `body_mode="source_image"`，随后调用 `tiyouju read-cuts <paper>` 提交内置看图队列。
4. **局部调整、安全重切与撤销**：
   - 局部微调使用 `tiyouju layout <regions|renumber|split|merge|undo>`。
   - 批量重切可加 `--replace-scope page|group|paper`，后端强制隔离**已通过、已入库或已编辑**的题卡，绝不覆盖人工成果，且整批切题操作均支持 `tiyouju layout undo` 原路撤销。

## 补标签和参考答案

本节是用户明确调用 CLI/MCP 的助手流程：当前豆包工作版或当前助手自己看图、分类、解题，再用本地 `enrich` 命令或 MCP 交回候选或答案初稿；**不需要配置豆包 API**。先检测安装版命令，再启用所需开关并准备指定题目的任务。旧名称标签协议只提交当前目录的知识点候选，任务完成后仍需人工采纳；答案初稿仍需核对并保存，不改原答案或人工审核记录。细节见 [question-workflow.md](references/question-workflow.md#补知识点和参考答案)、[commands.md](references/commands.md#助手补标签与参考答案)。

“设置 → API 配置 → 标签与答案”设置功能与独立答题 API。外层只有“API 配置、显示与导出、数据备份与迁移、帮助、关于”五区；旧 `/settings#ai`、`#api` 仍直接打开同一个配置窗口的答案分区。网页使用独立 API，CLI/MCP 助手任务仍由当前助手领取完成。标签只主动请求单题或明确批量范围，不配置 `enrich auto --tags on`；答案自动排队属于另一项兼容能力，仅在用户明确授权后使用。整批逐题、按每种任务分别 `prepare/submit`，不自动扫历史题，不声称豆包已常驻或识读前已有答案。

网页生成使用独立模型 API；明确调用的 CLI/MCP 助手流程可以另行执行。用户选择 API 后可推荐 DeepSeek，允许用户选择其他模型。核实实际接口与模型名，不把“DeepSeek Pro”当作已核实的官方 ID，不自动回退到读题模型或付费服务。不支持本机操作的客户端照实说明，可提供待导入结果，不宣称已写入；MCP 支持必须按当前客户端核实。

## 和老师一起干活

助手可以领活、交提案。用户会在题库页里看到一块「助手工作台」，上面列着助手交上来的待核对改动、谁在干活、采纳和不采纳各多少次；用户在那块里点「采纳」或「不采纳」。

- 还没入库的题照旧走 `fix` 流程，那本来就是导入流程，用户会逐题核对原卷。
- 已经入库的题**题面勘误**用 `propose` 交提案，不要直接 `fix`；若只提议修改 `answer`/`analysis`，用户在工作台点采纳会直接写入版本化解析层，不会把源题卡打回待核对。
- **严禁用 `propose` 批量灌整本答案书**。将已有标准答案书/官方解析批量写入正式题库，一律先在本地整理映射 JSON，用 `tiyouju solutions import --file <json> --dry-run` 干跑预览并由用户抽查 5–10 题（确保单选/多选/填空/判断题填好 `answer` 而非全塞进 `analysis`），确认无误后再正式写入（直写版本化解析层，保护已有解析且支持 `tiyouju solutions undo` 整批撤销）。
- 领活用 `claim`，干不完用 `release`。租约 30 分钟，助手断了活自动回池。
- 动手前先 `whereami` 问一句用户在哪儿；用户正在改的题绕开。
- 不做无人值守自动通过，不自动入库，不删题，不撤回入库。

命令与字段见 [references/commands.md](references/commands.md) 的「助手和老师一起干活」一节。

## 整理题库文件夹

题面文字和答案仍按上文走 `propose` 提案；**文件夹归档、排序、资料名/类型、教材章节目录与标签个人分组**属于组织结构，可通过 `tiyouju folders`（或对应 MCP 工具）**直接生效并支持撤销**，且会自动与题库工作台网页双向同步：

- **我的文件夹与大一统自由树**：`tiyouju folders list` 查看全树；`create` / `rename` / `move` / `delete` / `add` / `remove` / `sort` / `reorder` 建夹建子夹（最多 **8 层**）、移动文件夹或把 `paper:<id>` / `book:<id>` 资料夹自由归类到任意主题夹或根目录、手动排同级先后顺序（`manual`）。
- **书本纯数据驱动目录与整树原子提交**：`tiyouju folders apply-tree --file tree.json [--root book:<id>] [--dry-run]` 一次性原子提交整本书或多级专题目录树（支持空目录、树内挂载资料、稳定 ID 复用、可选 `--sync-sections` 同步 `section` 且不改题面版本），并默认开启该书的纯数据驱动自定义目录接管（可用 `tiyouju folders custom-tree <paper> on|off` 单独切换），彻底摆脱前端硬编码页码表与正则限制；整批提交支持单步 `tiyouju folders undo` 撤销。
- **教材章节/小节目录**：未入库题卡可用 `tiyouju fix <paper> <n> --section "..."`；已入库教材可用 `tiyouju folders set-section` 批量按章节规整，不改动题干或审核状态。
- **试卷/教材夹**：`tiyouju folders paper <paper> [--name 新名称] [--type exam|book]` 重命名或切换「试卷 ↔ 教材」。
- **标签个人分组**：`tiyouju folders tag-group <list|create|rename|delete|add-tags|remove-tags|set-tags|undo>` 整理个人分组及组内标签，不改变知识点上下级。
- **撤销**：`tiyouju folders undo` 撤销最近一次文件夹或章节整理；`tiyouju folders tag-group undo` 撤销个人分组改动。

命令与参数细节见 [references/commands.md](references/commands.md#整理题库文件夹)。

## 和 AI 共研题目、统计查重、组卷与复盘 OneNote 笔记

用户可以通过 CLI 或 MCP 直接让当前助手配合查题、讨论、总结规律、防撞车组卷与复盘手写笔记：

1. **全景统计与单题共研**：`tiyouju stats [--paper <p>]`（MCP `library_stats`）统计全库或单份资料的题型/审核/答案/OneNote 分布；未入库题用 `tiyouju show <paper> <card>`（MCP `show_card`）；已入库题支持直接粘贴 `Q-xxxxxxxx` 短码、UUID 或定位文本，用 `tiyouju locate <ref>`（MCP `locate_question`）秒查题干与原卷图，或用 `tiyouju detail <ref>`（MCP `get_question_detail`）一次查看版本历史、一题多源证据、已保存答案解析与原卷图。
2. **直读题库、标签树与防撞车选题**：结合 `tiyouju papers`（`list_papers`）、`tiyouju cards [--summary]`（`list_cards`）、`tiyouju tags`（`list_tags` 只读查看标签目录）、`tiyouju library --paper <p> --type <t> --sort ... --offset ... --exclude-used-days <d> --has-analysis --untagged`（MCP `search_library`）以及 `tiyouju usage`（`list_exam_usage` 查看近期用题记录），批量分析试卷命题特点并筛选不撞车的好题。
3. **重题只读扫描与变式题组**：用 `tiyouju dedup scan`（MCP `scan_duplicates`）只读发现相似/重复题簇与变式建议（**绝不自动合并或删题**），并用 `tiyouju variants bind/unbind`（`bind_variants`/`unbind_variant`）管理可互换变式题组。
4. **智慧组卷草稿与备份**：用 `tiyouju drafts list|show|create|update`（MCP `list_drafts`/`get_draft`/`save_draft`）把选好的题目列表（支持 `Q-xxxxxxxx`）和 A4/A3 双栏排版参数直接保存成组卷草稿（**绝不删除已有草稿**），供用户打开预览一键打印导出；用 `tiyouju backup`（`create_backup`）按需备份。
5. **OneNote 深度协同（严格只读内容，不改已有笔记）**：运行 `tiyouju onenote list [--paper <p>] [--q 关键词]`（`list_onenote_notes`）查看关联笔记，再用 `tiyouju onenote read <ref>`（`read_onenote_note`）提取文字块与手写墨迹截图为老师总结思路和提复习建议；需要老师当场手写或新开一页时，可用 `tiyouju onenote open <ref>`（`open_onenote_note`）或 `tiyouju onenote create <ref>`（`create_onenote_note`）。

## 不能运行命令时

支持电脑操作的助手可使用软件界面，但界面打勾记为**人工通过**，只有用户明确同意才这样操作。不支持本机命令或电脑操作的助手应说明限制，提供用户可执行的步骤，不声称替用户安装成功。
