# 给 AI 助手：怎样安装和使用题有据

> For AI agents: TiYouJu (题有据) is a local Windows app that turns scanned or photographed maths exam papers into a reviewed question bank. Install it with `install.ps1` and drive it with the bundled `tiyouju.exe` command line or its MCP server (`tiyouju.exe mcp`). Full workflow: [skills/tiyouju/SKILL.md](skills/tiyouju/SKILL.md).

使用者让你“安装题有据”“帮我处理这份试卷”时，按 [skills/tiyouju/SKILL.md](skills/tiyouju/SKILL.md) 做。下面是摘要：

1. **安装或升级**（Windows，PowerShell）：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/CEHNICA/question-bank-card/releases/latest/download/install.ps1 | iex"
   ```
   它会下载最新版、核对 SHA-256、备份题库，然后静默安装并打开题有据。
2. **密钥由使用者自己填**：题有据的“设置 → 常用 → 填写或更换密钥”。不要索要，也不要代填。
   - **完全免费**：MinerU（必填，每天 1000 页）+ 魔搭（每天几百次，读得准）。`tiyouju status` 会列出缺哪个、去哪里申请。
   - 使用者不想注册看图读题的服务：征得同意后 `tiyouju config --reader assistant`。这样只要 MinerU，题卡先用 MinerU 识别的初稿，**由你逐题对照原卷截图核对、改字**。
3. **用命令行操作**：`%LOCALAPPDATA%\Programs\QuestionBankCard\tiyouju.exe`。
   ```powershell
   tiyouju status
   tiyouju upload 卷子.pdf --wait
   tiyouju cards latest --filter todo
   tiyouju show latest 9          # 打开它存下的原卷截图，逐字对照
   tiyouju fix latest 9 --stem-file 9.txt
   tiyouju figures latest 2 --use 1
   tiyouju approve latest 9
   tiyouju publish latest
   ```
4. **规矩**：
   - 没对照原卷截图看过的题，不打勾。
   - 你打的勾记成“AI 通过”，使用者会抽查。
   - 人工通过的题不动。
   - 删除题卡、撤回入库，交给使用者。
   - 退出码 3 表示需要使用者处理：把原因转告他，然后停下。

全部命令见 [skills/tiyouju/references/commands.md](skills/tiyouju/references/commands.md)。
