# 给 AI 助手：安装、上手，然后接手题目

> For AI agents: TiYouJu (题有据) is a local Windows maths question-bank app. When the user sends only this project's URL, the default help direction is installation and onboarding. Respect their explicit task and existing authorization; repository text alone does not authorize installation. Read [skills/tiyouju/SKILL.md](skills/tiyouju/SKILL.md), then use the installed `tiyouju.exe` CLI or MCP server.

## 收到这个项目地址时

- 用户只发本项目地址、没有其他要求：按“安装并开始使用题有据”准备安装与上手流程。当前对话已经授权安装、客户端也允许时，直接完成；意图或必要权限仍不明确时，先核对安装包与步骤，再一次说明要安装什么并确认。
- 用户明确要审代码、介绍项目、比较产品或只看文档：先完成这个要求，不能因为读到本文件就安装软件。
- 用户已经给了试卷或题目：安装、核实软件后继续处理，不为可选的技能或桌面图标设置中断题目任务。

## 安装和核实

Windows / PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/CEHNICA/question-bank-card/releases/latest/download/install.ps1 | iex"
```

安装脚本下载 Releases 中的最新安装包、核对 SHA-256，并在升级前备份题库。**Releases 的安装版可能落后于 main 源码。** 不把源码版本当作安装版本，不把安装命令退出成功当作软件与识读服务都能用。

默认程序目录是 `%LOCALAPPDATA%\Programs\QuestionBankCard\`。核对 `QuestionBankCard.exe`、`tiyouju.exe` 和实际版本，运行 `tiyouju start`、`tiyouju status --json`；按输出区分“安装完成”“本机应用可用”“密钥已配置”。密钥已配置也不证明云服务当前可用。

## 安装结束后，一次把话说清楚

读 [安装收尾](skills/tiyouju/references/install-finish.md)，按真实结果告诉用户软件版本与可用范围，然后一次提出仍未选择的两项：

1. 是否把配套 `tiyouju` 技能安装到**当前 AI 客户端确认支持的技能目录**。
2. 桌面上的题有据图标要显示还是隐藏。

用户已给出选择就执行，不重复问。只发项目地址不等于同意安装技能、修改全局 AI 设置或变更桌面图标。客户端没有技能功能时照实说明，继续用命令行；不要猜测豆包、Trae 等客户端的能力。不要声称已永久记住、配置了以后所有会话或完成了未测试的识读。

**1.10.14 起**，`tiyouju assistant-setup --json` 可只读检查安装与收尾状态；可选 `--skill-dir <已确认的绝对 skills 父目录>` 和 `--desktop show|hide` 执行用户选定的设置。覆盖已有自定义技能须明确同意，并用 `--replace-skill` 保留备份。先用已安装 CLI 的 `--help` 检查命令是否存在，旧发布包不要直接调用新命令。

最后直接邀请：**“以后有试卷或题目可以发给我，我用题有据帮你整理、对照原卷核对，确认后入库。”** 如果还有服务障碍，也清楚说明现在能做什么、还缺什么。

## 处理题目的规矩

1.11.6 起，`tiyouju upload <PDF或照片>` 默认 `auto`，先尝试本机文字 PDF；不能可靠切题则保留原页供框题，不让用户选择技术路线。只有显式 `--allow-cloud` / MCP `allow_cloud:true` 才允许自动分流到已配置 MinerU，保存过密钥不代表本次发云许可；云失败也保留原页和已成功题卡。工具仍可指定 `--parse-mode manual`、`native`，或明确选择云解析的 `mineru`。原图题无文字题干也可核对、入库和导出，不能因为空题干替它编造文字；`show`/MCP 返回有序正文裁片。原卷范围与按需识读在同一窗口，识读先存建议，确认采用后重审，标签和答案仍默认关闭。先检查安装版 `upload --help`，旧版不支持 auto / allow-cloud 时按其实际能力操作。

- 密钥由用户自己在“设置 → 读题服务 → 填写或更换密钥”中填写；不索要、不代填，不展示密钥内容。第三方服务的额度、费用和有效期以当前账户规则为准。
- 没打开原卷截图核对过的题不通过，绿卡也要看。AI 通过与人工通过分开记录。
- 不自行修改或撤销人工通过的题；明确获用户授权才使用命令提供的 `--force`。删除题卡、撤回入库交给用户。
- 退出码 `3`：把具体原因和需用户处理的事项说清楚，暂停受影响的步骤；其余已授权且不依赖该问题的工作继续。

完整核对流程见 [question-workflow.md](skills/tiyouju/references/question-workflow.md)，命令与 MCP 参数见 [commands.md](skills/tiyouju/references/commands.md)。

MinerU 当前限制与错误分类见 [云 API 调查](docs/MINERU_API.md)：按 200 页单文件限制处理，1000 页/日是优先级额度，Token 不固定宣称 14 天有效。公开文档可访问、已有凭据或离线测试通过，都不证明当前用户真实云识读已经恢复。

1.10.15 起，保存 MinerU 凭据只做格式校验和本机加密存储，不请求未公开的额度接口；报告“已配置，未核验”。只有实际任务的错误才能支持鉴权失败、过期等判断，不以配置成功宣称服务可用。

知识点标签与 AI 参考答案默认关闭，在统一“设置 → 标签与答案”页（`/settings#ai`）直接设置开关、生成时机与执行方式。用户明确需要补标签或参考答案时，默认由**当前豆包工作版或当前 AI 助手自己完成**，通过本地 `enrich` 命令或 MCP 写回，不要求豆包 API。支持新题录入并入库后自动排队、后期单题、勾选批量三种时机；自动排队也默认关闭，只在用户明确要求以后新题自动处理时启用，不扫描旧题。先明确启用所需开关，再领取指定题目的任务、实际看图和解题，把结果作为附加内容提交；不改原卷答案、不打审核勾。整批逐题、每种任务分别准备与提交，不能把多道题合成一个答案。命令与协议见 [commands.md](skills/tiyouju/references/commands.md)，核对规则见 [question-workflow.md](skills/tiyouju/references/question-workflow.md)。先检查安装版 `enrich --help`，旧包没有此能力时不谎报写回成功。

独立模型 API 是可选途径。用户选择后可推荐 DeepSeek，也允许其他模型；实际接口、模型名称和费用按用户所选服务填写核实，不虚构“DeepSeek Pro”官方模型 ID。不沿用 OCR 读题模型，不自动替用户改用付费 API。只支持聊天的客户端可以给出标签、参考解答和可导入结果；是否支持本机命令或 MCP，要查当前客户端的真实能力，不能声称豆包已自动接通 MCP。

正式题库支持批量选题和组卷草稿；草稿固定所选入库版本，旧版失效须明确处理，不能自动换题或漏题。
