# Baseline-Record.md —— 题有据标签系统重构：正确基线与隔离环境

记录时间：2026-10-25（用户时区 America/Los_Angeles）
执行范围：开发、隔离验收、候选构建。**未**替换安装版、**未**迁移真实题库、**未**批量采纳标签、**未**对外发布。

---

## 1. 为什么不能拿旧源码直接开工

计划文件里给了一个旧源码目录，并明确要求先确认它是不是当前版本。核实结果：

| 目录 | 版本 | HEAD | 分支 | 结论 |
|---|---|---|---|---|
| `<本机路径>\Documents\Codex\2026-10-08\question-bank-card-pdf-fix` | 1.22.0 | `8c4d6c6851d17250614290c875cbbaccd5deffe0` | `feature/original-paper-layout` | **旧版，只能参考，不能当基线** |
| `<本机路径>\Documents\Codex\2026-09-28\题有据改进\question-bank-card` | 1.10.9 | `886227e6815fb53342161c73fde00d2107b52004` | `improve/1.10.0` | 更旧 |
| `<本机路径>\Documents\破甲\qbc-latest` | 1.26.6 | `e1d71dbec227` | `main` | 落后，且有 9 个未提交改动 |
| GitHub `CEHNICA/question-bank-card` main | 1.26.28 | `f945d59d8007` | `main` | **比安装版旧** |

安装版实测：`tiyouju.exe --version` → `tiyouju 1.26.31`（安装目录 `<本机路径>\AppData\Local\Programs\QuestionBankCard`）。

GitHub Releases 最新只到 `v1.26.28`（2026-10-25 04:10 UTC 发布），全部 24 个分支里没有任何一个版本号 ≥ 1.26.29。**所以 1.26.31 是本机构建、尚未推送的版本，只能从本机找。**

## 2. 正确基线

```
仓库：   <源码仓库>
分支：   main
HEAD：   ccdb8f00f2a5f655ad6e4216bfc74e5654ab05b3
版本：   1.26.31（backend/core/version.py）
远端：   领先 origin/main 5 个提交（未推送）
```

HEAD 往前的提交：

```
ccdb8f0  合题再拦一道：题型不一样的不让合
f063b5b  1.26.31：拆题的入口删掉；合题把两段文字接成一道
3984095  题目来源（题源）：认得出来了，也改得动了
16a23b4  1.26.29：补题的题组改按原卷题号段判断，别拿组内题号比大小
ff6285f  1.26.29：切题时不再让老师替程序选题组；题组名改成位置标签
f945d59  1.26.28：给项目装上专业开发环境，顺带修一个会悄悄丢东西的问题
```

仓库里另外两个 worktree（`D:\qbj_1238` 在 `fix-quit-hang` 分支、`D:\qbj_rel_12630` 在 1.26.29 的 detached HEAD）**没有动**。

## 3. 主仓库里那 3 个未提交改动 —— 处理方式

`git status` 在基线仓库里显示 3 个已改文件：

```
 M backend/core/pipeline.py
 M backend/core/test_anchor_flag_reconcile.py
 M backend/core/test_anchor_reading.py
```

**内容**：给 `textnorm.anchor_reading_mismatch` 增加第三个参数 `origin`，让判据比对时把已经拆到「题源」字段里的出处排除掉；同时给「已经挂上这条误报的卡」加上撤回逻辑。属于认题旗标判据的修复，跟标签重构不是一件事。

**判断这是用户当前在用的代码，不是别人的半成品**：

| 证据 | 说明 |
|---|---|
| 文件 mtime 2026-10-24 21:54 | 早于安装版 exe 的 22:43 构建时间 → 这些改动**已经进了 1.26.31 安装版** |
| `textnorm.py` 里 `anchor_reading_mismatch(printed, reading, origin="")` 的三参数签名已在 HEAD 里 | 改动自洽，不会运行时报错 |
| 改动带完整测试（两个 test 文件共 181 行新增） | 是写完的功能，不是调试残留 |

**处理**：主仓库工作区**一个字节都没动**。改动以 `git diff` 导出成补丁
`work/baseline-uncommitted.patch`（14903 字节），在新 worktree 里 `git apply` 重放。

## 4. 隔离环境

```
worktree： <本机路径>\Documents\Codex\2026-10-24\ben\work\question-bank-card
分支：     feature/tag-system-refactor（从 ccdb8f0 开）
```

隔离靠三件事，都跟生产环境分开：

| 隔离项 | 生产 | 本次开发 |
|---|---|---|
| 数据库 | `<本机路径>\AppData\Local\QuestionBankCard\db.sqlite3`（53.5 MB，验收期间仍在被写） | `QB_DATABASE` 环境变量指向 worktree 内的独立 sqlite |
| 数据目录 | 同上 | `QB_DATA_ROOT` 指向独立目录 |
| 端口 | 8768 | 8799（开发实例） |
| 凭据 | 用户真实密钥 | 不读取、不写入、不打印 |

**生产库全程只读、且只读副本**：设计决策所依据的标签数据统计，取自
`<本机路径>\AppData\Local\QuestionBankCard\backups\db-before-1.26.29-20261024-210545.sqlite3`
（1.26.29 迁移前的备份），以 `file:///...?mode=ro` 只读打开。没有对生产库执行任何写操作。

### 基线一致性核对

补丁重放后，逐文件 SHA-256 与主仓库比对：

```
backend\core\pipeline.py                      IDENTICAL
backend\core\textnorm.py                      IDENTICAL
backend\core\test_anchor_flag_reconcile.py    IDENTICAL
backend\core\test_anchor_reading.py           IDENTICAL
backend\core\library_tags.py                  IDENTICAL
backend\core\library_tagging.py               IDENTICAL
backend\core\version.py                       DIFFER  ← 仅 CRLF/LF 行尾差异，内容一致
```

`version.py` 的差异经 `git diff --no-index` 确认只有行尾符（Git 的 `core.autocrlf` 提示），`APP_VERSION` 两边都是 `1.26.31`。

## 5. 基线测试结果（改动前，原始基线）

在**打标签系统补丁之前**跑了一遍完整门禁，作为「原有失败」与「本次引入的失败」的分界：

| 项目 | 结果 |
|---|---|
| 后台测试 `manage.py test core` | 通过（退出码 0） |
| 前端测试 `node --test frontend/test_*.js` | **139 个测试，139 通过，0 失败** |
| 桌面外壳测试（unittest，8 个模块） | **147 项通过，OK** |
| 总体退出码 | **0** |

基线是干净的：没有需要继承的既有失败。

## 6. 运行入口

| 用途 | 命令 |
|---|---|
| 开发实例 | `$env:QB_DATABASE=...; $env:QB_DATA_ROOT=...; python backend/manage.py runserver 127.0.0.1:8799` |
| 全套检查 | `.\dev.ps1 check`（lint + type + test，逐步看退出码） |
| 打包 | `.\dev.ps1 build -Version <v>` |
| 验收脚本统一入口 | `python tools/run_acceptance.py --base http://127.0.0.1:8799` |

## 7. 通过条件核对

- [x] 不是 1.22.0 冒充新基线 —— 基线是 1.26.31 的 `ccdb8f0`
- [x] 隔离实例可以启动 —— 见 `Acceptance-Record.md`
- [x] 生产服务不受影响 —— 生产 8768 全程在听，未被本轮操作触碰
- [x] 别人/用户的未提交修改没被覆盖 —— 主仓库工作区零改动，改动以补丁形式重放
