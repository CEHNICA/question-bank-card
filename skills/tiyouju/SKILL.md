---
name: tiyouju
description: 安装和操作题有据 Windows 本机软件，把数学试卷、题目整理成可追溯题库。用户发题有据项目地址、要安装或升级题有据、处理试卷、核对题卡、整理题库时使用；显式要求审代码或介绍项目时先按该要求工作。
---

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
- 不索要、不代填密钥。缺密钥时请用户自己在“设置 → 常用”填写；第三方额度、费用、有效期以当前账户规则为准。
- 更改读题方式先取得用户选择；`config --reader assistant` 只省去看图模型，处理新原卷仍需 MinerU。已有题卡和示例教学不因此失去可用性。
- `--json` 输出便于解析，中文为 ASCII 转义；不加时为 UTF-8。`--agent 你的名字` 记录 AI 身份。
- 退出码 `0` 成功、`1` 出错、`2` 应用未启动（用 `start`）、`3` 需用户处理。遇 `3` 说明具体原因，暂停受影响步骤，继续独立且已授权的工作。

题目任务结束时报告总题数、修改内容、待用户处理的题和原因、入库数。邀请用户继续发题；若识读服务未恢复，明确说可先核对已有题卡，恢复后再处理新资料。

## 不能运行命令时

支持电脑操作的助手可使用软件界面，但界面打勾记为**人工通过**，只有用户明确同意才这样操作。不支持本机命令或电脑操作的助手应说明限制，提供用户可执行的步骤，不声称替用户安装成功。
