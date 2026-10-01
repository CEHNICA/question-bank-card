# 题有据 v1.10.6

上传的试卷交给 MinerU 以后，页面上显示 MinerU 自己说的状态，等得久时能看出是在等谁。

## MinerU 解析这一步

以前这一步只写“正在准备文件或等待 MinerU 返回；MinerU 没有提供完成百分比”。一份 4 页的月考卷平时 10–20 秒就过去了；MinerU 那边忙的时候要等好几分钟，页面上看不出是题有据卡住了，还是 MinerU 在排队。

现在按 MinerU 的回答显示：

- **正在把文件传给 MinerU**
- **在 MinerU 排队中 · 已等 2分10秒**：文件已经交上去了，在等 MinerU 开始识别。要等多久看 MinerU 当时有多忙，题有据没有卡住。
- **MinerU 识别中 · 第 3/4 页**：进度条跟着走。
- **MinerU 识别完了，正在打包结果**、**正在取回 MinerU 的结果**：马上进入本机切题。

一本书分片解析时照旧显示“已完成几个分片、正在处理第几页到第几页”。

MinerU 一直没有结果时，20 分钟后这份试卷会显示解析超时，点“重试”重新交给 MinerU。

## 升级

已经装了题有据的，下载新的安装包直接覆盖安装即可，题库和设置都会保留。也可以运行：

```powershell
irm https://github.com/CEHNICA/question-bank-card/releases/latest/download/install.ps1 | iex
```

它会下载最新版、核对 SHA-256、先备份题库，再静默安装。正在解析的试卷，升级后会重新交给 MinerU。
