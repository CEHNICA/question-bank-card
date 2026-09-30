# 真实 API 基准测试（开发者工具）

这两个脚本用真实的 MinerU / MiniMax 账号跑完整流程，用来比较改动前后的**速度**和**识读质量**。它们不随安装包发布，也不会读写正式题库。

> 会消耗你自己账号的额度。样卷、结果目录和 `secrets.json` 都不要提交到仓库（`.gitignore` 已排除 PDF、数据库；`secrets.json` 请放在仓库外）。

## 准备

在仓库**外**新建一个目录，例如 `D:\qb-bench\`，放入：

- `secrets.json`：`{"mineru": "你的 MinerU Token", "minimax": "你的 MiniMax Key"}`（可加 `"siliconflow"`）
- 样卷：PDF、Word，或几张照片

## 1. 探测 MiniMax 账号能承受多少并发

```powershell
python tools\benchmark\probe_minimax.py --secrets D:\qb-bench\secrets.json --image D:\qb-bench\一道题的截图.jpg --levels 1,2,4,8
```

输出每个并发档位的耗时、HTTP 状态和是否出现 429。结果写入 `probe.json`。

## 2. 跑完整流程

```powershell
python tools\benchmark\qb_bench.py --repo . --out D:\qb-bench\runs\v1 --secrets D:\qb-bench\secrets.json ^
  D:\qb-bench\卷1.pdf D:\qb-bench\卷2.pdf "D:\qb-bench\p1.jpg|D:\qb-bench\p2.jpg"
```

- 多张照片用 `|` 连起来，算作一份照片卷。
- 每份卷的结果在 `runs\v1\cards\<卷名>\`：`result.json`（每张题卡的三次识读、最终题面、黄卡原因）、每题原卷截图 `qNN_*.jpg`、页面预览。
- `runs\v1\events.json` 记录每次 API 调用的起止时间、状态码和用量；`summary.txt` 是每份卷的耗时与红黄绿统计。
- `--env KEY=VALUE` 可临时改配置，例如 `--env QB_MINIMAX_ACCOUNT_CONCURRENCY=8`、`--env QB_HEDGE_AFTER=0`。

- `--worker`：像应用的后台工作者那样，先把所有卷一起排队，再用主车道、提前解析车道和“重叠读题”车道处理，测的是一批卷的总耗时。加 `--no-overlap` 可以关掉重叠读题做对照。

比较两个版本时，对同一批样卷分别用两个仓库目录（`--repo`）各跑一次，再逐题对照 `result.json` 与截图。
