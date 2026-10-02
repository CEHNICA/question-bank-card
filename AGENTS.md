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

- 密钥由用户自己在“设置 → 常用 → 填写或更换密钥”中填写；不索要、不代填，不展示密钥内容。第三方服务的额度、费用和有效期以当前账户规则为准。
- 没打开原卷截图核对过的题不通过，绿卡也要看。AI 通过与人工通过分开记录。
- 不自行修改或撤销人工通过的题；明确获用户授权才使用命令提供的 `--force`。删除题卡、撤回入库交给用户。
- 退出码 `3`：把具体原因和需用户处理的事项说清楚，暂停受影响的步骤；其余已授权且不依赖该问题的工作继续。

完整核对流程见 [question-workflow.md](skills/tiyouju/references/question-workflow.md)，命令与 MCP 参数见 [commands.md](skills/tiyouju/references/commands.md)。

MinerU 当前限制与错误分类见 [云 API 调查](docs/MINERU_API.md)：按 200 页单文件限制处理，1000 页/日是优先级额度，Token 不固定宣称 14 天有效。公开文档可访问、已有凭据或离线测试通过，都不证明当前用户真实云识读已经恢复。

1.10.15 起，保存 MinerU 凭据只做格式校验和本机加密存储，不请求未公开的额度接口；报告“已配置，未核验”。只有实际任务的错误才能支持鉴权失败、过期等判断，不以配置成功宣称服务可用。
