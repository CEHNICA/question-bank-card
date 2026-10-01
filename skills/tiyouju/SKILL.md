---
name: tiyouju
description: 操作“题有据”——把扫描、拍照的数学试卷变成核对过的题库的 Windows 本机软件：安装或升级、完全免费的配法、上传试卷、逐题对照原卷截图核对文字和配图、改字、打勾通过、入库、查题库。用户提到题有据、试卷录入、整理题库、“帮我处理这份试卷”时使用。
---

# 题有据（tiyouju）

题有据把 PDF、Word、手机照片里的数学题切成一道一道的“题卡”：左边是原卷截图，右边是 AI 读出来的文字。你的工作是**像一个认真的老师一样，逐题对照原卷截图核对文字**，错了就改，对了就打勾，最后入库。

你通过命令行 `tiyouju.exe` 操作它（在 `%LOCALAPPDATA%\Programs\QuestionBankCard\tiyouju.exe`）。加 `--agent 你的名字`（例如 `--agent 豆包`），题卡上会显示“豆包 通过”。

每个命令加 `--json` 会输出 JSON。JSON 是纯 ASCII，中文写成 `\uXXXX`，在任何终端里都不会乱码，推荐用它读结果。不加 `--json` 时输出 UTF-8 中文；如果看到乱码，先运行 `chcp 65001`，或者改用 `--json`。

退出码：`0` 成功，`1` 出错（看输出的原因），`2` 题有据没打开（运行 `tiyouju start`），`3` 需要使用者处理（把输出原话转告使用者，然后停下）。

## 第一步：确认能用

```powershell
& "$env:LOCALAPPDATA\Programs\QuestionBankCard\tiyouju.exe" status
```

- 提示找不到文件：还没安装。用下面这一行安装（下载最新版、核对 SHA-256、备份题库、静默安装）：
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/CEHNICA/question-bank-card/releases/latest/download/install.ps1 | iex"
  ```
  GitHub 下载不动时，先问使用者有没有能用的 GitHub 加速地址，设到环境变量 `TIYOUJU_MIRROR` 里再运行。
- 退出码 2：运行 `tiyouju start` 打开它。
- “可以上传新卷子：否”：密钥没填。`status` 会列出缺哪个、去哪里免费申请。把这些转告使用者，**请使用者自己**在题有据的“设置 → 常用 → 填写或更换密钥”里填写。不要向使用者索要密钥，也不要自己填写。完全免费的配法见下一节。
- 想升级，再运行一次上面的安装命令。已经是最新版，它会直接说明。

下面的例子都写成 `tiyouju`。实际运行时用完整路径，或者先 `cd` 到它所在的文件夹。

## 完全免费的配法

题有据要两样东西：MinerU 把卷子切成一道一道的题；再有一家“看图读题”的服务，看着截图把题读成文字。都有免费的：

| 服务 | 要不要 | 免费额度 | 去哪里申请 |
| --- | --- | --- | --- |
| MinerU | 必填 | 每天 1000 页。Token 14 天过期一次，过期了重新生成一个 | https://mineru.net/apiManage/token |
| 魔搭 | 推荐，读得准 | 每天几百次。要先在魔搭头像菜单里“绑定阿里云”，阿里云用支付宝扫码实名 | https://www.modelscope.cn/my/myaccesstoken |

- 魔搭当天的免费额度用完了，还没读的题会先用 MinerU 的初稿（标黄，疑点写着“看图读题的服务这会儿用不了”）。这些题和 AI 助手读题一样逐题核对、改字；或者等第二天额度恢复，再 `tiyouju reread`。
- 使用者也填了付费的 MiniMax 或硅基流动时，某一家用不了会自动换另一家接着读，不用管。
- MiniMax、硅基流动是付费的。使用者已经有，就照常用。硅基流动目前没有能读题的免费模型。
- 使用者连看图读题的服务也不想注册：征得他同意后，改成 **AI 助手读题**，这样只要 MinerU：
  ```powershell
  tiyouju config --reader assistant     # 改回看图读题：tiyouju config --reader modelscope
  ```
  改动从下一份新上传的卷子开始生效。`tiyouju config` 不加参数，只看现在的设置。

## AI 助手读题：题卡上的字由你来定

选了 AI 助手读题，题有据就不再看图读题。每张题卡的文字是 MinerU 自己识别的**初稿**，题卡是黄色的，疑点写着“题面是 MinerU 识别的初稿”（JSON 里 `text_source` 是 `mineru`）。

这时你是唯一看过原卷的读者。每一道题都要按下面“怎么核对一道题”逐字对照原卷截图：

- MinerU 常见的问题：公式丢了 `$...$`、上下标和分数读错、选项挤在一行或者漏了、表格错格、把页眉、分数栏、学生手写的字读进来。
- 有错就用 `fix` 把题干和选项改对（选项只写内容）。保存以后，“初稿”这个疑点就没了。
- 初稿完全正确，也要看过原卷截图再 `approve`。
- 配图照常用 `figures` 处理。
- `reread` 在这种模式下只会重新生成一遍 MinerU 初稿，没有用。

## 处理一份试卷

```powershell
tiyouju upload "D:\试卷\期中.pdf" --wait     # 上传并等它读完（几分钟）
tiyouju cards latest --filter todo           # 需要逐题核对的题
tiyouju show latest 9                        # 看第 9 题：文字 + 原卷截图
# ……核对、修改、打勾，见下面……
tiyouju cards latest --filter green          # 识读一致、还没通过的题：也要看
tiyouju publish latest                       # 入库
```

- `latest` 指最近上传的一份。也可以写试卷编号的前 8 位，或者名字里的一段（`tiyouju papers` 列出全部）。
- 几张照片是同一份试卷时，一起上传：`tiyouju upload 1.jpg 2.jpg 3.jpg --wait`。是一本书或讲义时加 `--book`。
- `wait` 没等完就再运行一次。退出码 3 表示需要使用者确认资料结构，或者额度用完了，把原因转告使用者。

## 怎么核对一道题

`tiyouju show <试卷> <题号>` 会打印读出来的文字和疑点，并把图片存到临时文件夹，然后列出路径：

- `原卷截图`（`…-crop.png`）：这道题在原卷上的样子。**一定要打开看。**
- `候选图编号`（`…-candidates.png`）：原卷截图上用蓝框和数字 1、2… 标出了可能是配图的地方。
- `配图`（`…-figure1.png` 等）：现在配给这道题的图。

逐项对照原卷截图：

1. **数字和符号**：正负号、小数点、分数线、根号、指数和下标（x² 和 x₂），单位（cm、cm²、°）。
2. **字母**：大小写、相似的字母（l 和 1、O 和 0、x 和 ×）、角和三角形的写法（∠、△）。
3. **题干完整**：没有漏句子，也没有把下一题、页眉页脚、学生手写的答案读进来。原卷括号里手写的答案不要写进题干，留空括号。
4. **选项**：A–D（有的题有 E）一个不少，字母和内容对得上。
5. **表格**：逐格对照，空格子也要对得上。
6. **配图**：原卷上有图，题卡上也要有图，而且是这道题的图，放在对的位置（题干还是某个选项）。

7. **题型**：单选、多选、填空、判断、解答，和原卷对得上。疑点写着“题型还没定”的题**不能打勾**，先用 `fix --type` 选好题型。
8. **题源**：题干前印的出处（“[2026××中学月考]”“（2025·北京海淀·期中）”）不属于题目，题有据会自动放进“题源”；`show` 里单独列出。没拆出来就用 `fix --origin` 填上，并把它从题干里删掉。

疑点里的“两次识读不一致”“可能漏图”“配图冲突”要重点看。`show` 会列出几次识读的写法，以原卷为准。写法不同（例如 `$\triangle ABC$` 和 `△ABC`）不算错。

## 改字

写进一个 UTF-8 文本文件，再用 `--stem-file` 读进来。这样中文、引号、公式都不会被命令行弄坏：

```powershell
tiyouju fix latest 9 --stem-file 9.txt
tiyouju fix latest 9 --option B=-3 --option D=7
tiyouju fix latest 9 --clear-option E
tiyouju fix latest 9 --type free_response    # 只改题型：single_choice 单选 / multiple_choice 多选 / fill_blank 填空 / true_false 判断 / free_response 解答
tiyouju fix latest 9 --origin "2026山东枣庄滕州二中月考"   # 题源（题干前印的出处）
```

格式：

- 数学式用 LaTeX，行内用 `$...$` 包住：`$\frac{1}{2}$`、`$x^2$`、`$\sqrt{3}$`、`$\angle ABC$`、`$AB \parallel CD$`。
- 中文和中文标点照原卷写。填空的横线写 `____`，选择题的括号写 `（  ）`。中文句子里的引号写 “”（题有据也会自动把 "…" 换成 “…”）。
- 文字和数字组成的表格写成 Markdown 表格，每行一行，第二行是 `|---|---|`。
- 选项只写内容，不写“A.”。

改完再 `show` 一次，看看保存后的样子。

## 配图

```powershell
tiyouju figures latest 2 --use 1          # 图1 作为题干的配图
tiyouju figures latest 5 --use 1:A,2:B    # 图1 给选项 A，图2 给选项 B
tiyouju figures latest 7 --keep           # 现在的配图是对的，保留
tiyouju figures latest 3 --none           # 这道题确实没有图
```

没被选中的候选图，都会记成“不是这道题的图”。候选图里没有对的图时（例如框得不完整），不要硬选。把题号告诉使用者，请他在软件里点这道题的“配图”，自己框出来。

## 打勾和入库

```powershell
tiyouju approve latest 9            # 对照无误后打勾，可以一次写好几个题号
tiyouju approve latest --green      # 识读一致的绿卡一起通过
tiyouju publish latest              # 把通过的题入库
```

- **没有打开原卷截图核对过的题，不要打勾。** 绿卡也要逐题看。绿卡只说明两个 AI 读得一样，不代表读对了。
- 你打的勾记成“AI 通过”：题卡上显示“××已通过 · 待你核对”，入库后题库里标着“AI 审核”。使用者会抽查。
- 使用者已经人工通过的题，不要改，也不要撤销，命令行会拒绝。使用者明确让你改时，才加 `--force`。
- 打错了勾，用 `tiyouju unapprove latest 9` 撤销自己打的勾。
- 一道题怎么也读不对时，可以 `tiyouju reread latest 9`，让软件的读题模型重读一遍。重读完再 `show`。（AI 助手读题时不用 `reread`，直接 `fix`。）

## 什么时候停下来问使用者

- 退出码 3，或者输出里让使用者处理的事：资料结构要确认、额度用完、软件启动不了。
- 密钥没填、要换密钥，或者 MinerU 的 Token 过期了（14 天一次）。
- 要改读题方式（`config`）之前。
- 原卷截图本身不对：截少了、截到了别的题、题号乱了。请使用者在软件里点这道题的“调整范围”。
- 候选图里没有对的配图。
- 原卷印刷不清，你也拿不准是什么字。
- 删除题卡、撤回已入库的题：命令行不提供这些操作，由使用者在软件里决定。

最后向使用者汇报：一共几道题，改了哪几道（改了什么），哪几道需要他处理（为什么），入库了几道。

## 没法运行命令时

如果你只能看屏幕、点鼠标，就打开题有据的窗口：

- 左边“试卷”列表选卷子，工具栏的筛选点“需逐题核对”。
- 每张题卡：左边是原卷截图（点一下放大），右边是文字。
- 快捷键：`J` / `K` 上一张、下一张，`Enter` 通过并跳到下一张，`E` 改字，`F` 配图，`R` 调整范围，空格放大对照，`U` 撤销通过，`?` 看全部快捷键。

注意：在窗口里打的勾会记成**人工通过**。只有使用者明确同意时，才这样做。

## 给支持 MCP 的 AI

`tiyouju.exe mcp` 是一个 stdio MCP 服务器，工具和上面的命令一一对应（`configure_reading` 对应 `config`）。`show_card` 会把原卷截图直接作为图片返回。配置示例：

```json
{ "mcpServers": { "tiyouju": { "command": "C:\\Users\\<用户名>\\AppData\\Local\\Programs\\QuestionBankCard\\tiyouju.exe", "args": ["mcp"] } } }
```

全部命令和参数见 [references/commands.md](references/commands.md)。
