# 切题、配图与真实 API 基准测试（开发者工具）

`score_layout.py` 离线比较人工标注与切题、配图结果，不调用 API、不读取密钥、不修改题库。`qb_bench.py` 和 `probe_minimax.py` 则使用真实的 MinerU / MiniMax 账号比较完整流程的速度与识读结果。工具均不随安装包发布。

## 离线评分：题号、正文范围与配图关系

人工对照原页标出正确的题卡、完整正文范围和配图，保存成 `truth.json`。输入均为包含 `cards` 数组的 JSON 对象；预测文件可直接使用新版 `qb_bench.py` 导出的 `result.json`。

```powershell
python tools\benchmark\score_layout.py --truth D:\qb-bench\truth.json --result D:\qb-bench\runs\v1\cards\卷1\result.json --output D:\qb-bench\scores\v1.json
python -m unittest tools.benchmark.test_score_layout
```

省略 `--output` 时报告写入标准输出。非法数据返回退出码 2，并指出错误字段。此命令只读两个输入文件，写出指定报告，不启动真实 API 基准流程。

最小人工标注示例（`page_idx` 从 0 开始，`bbox` 使用与预测相同的页面坐标，应用输出通常是 0–1000 的归一化坐标）：

```json
{
  "cards": [
    {
      "group_sequence": 0,
      "source_kind": "unknown",
      "number": 1,
      "regions": [{"page_idx": 0, "bbox": [50, 100, 950, 400]}],
      "figures": [{"page_idx": 0, "bbox": [600, 200, 900, 350], "slot": "stem"}]
    }
  ]
}
```

- 题卡按 `(group_sequence, source_kind, number)` 对齐。`source_kind` 取实际输出的值；同号位于不同题组或来自例题/练习时分别计数。旧结果只有动态 `group` ID 时，需补充正确的 `group_sequence` 或重新导出；无题组的卡使用 `null`。跨版本比较时应固定人工题组划分及顺序。
- `question_numbers` 给出题号精确率、召回率、缺题、多余题及重复身份。人工标注中重复身份会报错；预测中的重复身份计为多余题，只使用输入中的首张匹配正文和图片，避免从重复结果中挑选表现最好的一张。
- `body_regions` 按每题、每页分别计算矩形并集，再累加面积。覆盖率是交集面积/标注面积，精确率是交集面积/预测面积；同一题内的重叠框不重复计面积。漏题和多余题分别降低覆盖率与精确率，同页不同题的范围作为独立归属计算。
- `figure_relations` 比较每张图所属的题卡及 `slot`（例如 `stem`、`A`、`B`）。页集合与槽位必须一致，矩形并集 IoU 至少为 0.5，使用一对一最大匹配；位置正确但 A/B 归属错位仍会扣分。`parts` 是主图以外的裁片，例如 `"parts": [{"page_idx": 1, "bbox": [50, 50, 400, 200]}]`；主图和全部裁片共同参与计算。公共图应分别写入每个确实引用它的题卡，关系按题独立计数。
- `cards` 给出每道标注题的正文面积、覆盖率及图像匹配明细。所有分母为 0 的指标输出 `null`；两份空样本也输出 `null`，不视作满分。输入坐标必须是有限数值，逆转、零面积或坏框会报错。

这些指标测量题卡身份、几何覆盖与配图归属，**不代表题干、公式、答案或数学语义的正确率**。请保留同一组人工标注，用同一批原页比较改动前后；暂无真实标注集时只能验证评分工具的计算规则。

## 切线自检：不需要人工标注

`audit_cuts.py` 不需要标注集就能回答“这次改动把切题改好了还是改坏了”。它读同一份 `qb_bench.py` 产出的每卷目录（`meta.json`、`blocks.json`、`pages/p001.png`…）和一份题卡预测，检查四件事：

- **题号**：印刷序列是不是一条干净的 1..N，缺的号有哪些（缺号本来就交给模型去找，所以单列出来而不是当成失败）
- **覆盖**：每页文字有多少落在某张题卡的范围内（余下的通常是大题标题和卷头，不是丢内容）
- **切线**：切线有没有从一行**印刷字**中间劈过去；有没有把下一题的题号吃进这一张
- **配图**：题干说了“如图”而这张卡没有图 —— 用户拿到的是一道做不出来的题

```powershell
python tools\benchmark\audit_cuts.py --papers D:\qb-bench\papers --pred D:\qb-bench\pred
```

`--expected-only` 只打印每卷一行汇总。发现切线问题时退出码为 1，可以当构建闸门用。“缺图”那一列同样只报不判失败：这些卡会被产品的 `blocked_missing` 状态拦下来等人工处理，不是切线的回归——但必须看得见，否则没人守它。

四点使用说明，都是实测踩出来的：

- **“切到字”只统计印刷字。** 手写铅笔、下划线、图形边缘的墨迹密度达不到印刷行的一半，一律不算“丢字”；否则实测会把 0 张错判成 22 张，再改成 87 张。铅笔的密度峰值在 0.18 左右，印刷行在 0.2 以上。
- **墨迹读数必须和切题代码走同一条路。** 工具内部调用 `cuts.row_ink`，阈值与生产代码完全一致；判断逻辑（多少算“丢了一半”）则是工具自己的，两边不共用。
- **只有“下一题的题号”算吃题。** 一张卡里出现 `4. 6 1 3` 或 `0. - k + 1)` 是小数和算式，不是第 4 题；源卷本身把“19.”印成“9.”时也不能一直报警。所以只有卡 N 里出现 N+1 才报。
- **题号在识别层就丢了的情况查不出来。** 上面那条例外：文字层整行缺失时没有任何文字可查，只能靠“缺号”那一列发现。改切题算法后请两个一起看。
- **“缺图”用的是产品自己的 `has_figure_cue`。** 被拦下来的正是这个条件，另写一份正则就是在测另一件事。它比朴素的正则宽一些，也会把“画出图中三角形的三条高”这种**让学生自己画**的指令算进来——产品内部对这种情况另有一层判断，这里只是粗筛，所以只报不判。

## 真实 API 流程

> 下列流程会消耗你自己账号的额度。样卷、结果目录和 `secrets.json` 都不要提交到仓库（`.gitignore` 已排除 PDF、数据库；`secrets.json` 请放在仓库外）。

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
