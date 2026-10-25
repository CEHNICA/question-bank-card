# 参与贡献

题有据是给中学数学老师用的本地题库工具：一个 Python（Django）后台 + 一套手写的
前端网页，打包成 Windows 桌面程序，**全程离线**，不上传任何题目数据。

这份文件说清楚三件事：**怎么把开发环境装起来**、**改完东西怎么确认没改坏**、
**一个版本怎么发出去**。

---

## 一、先把环境装起来

需要 **Windows + Python 3.12 + Node.js 22**（Node 只在开发期用，运行时零依赖）。

```powershell
git clone https://github.com/CEHNICA/question-bank-card.git
cd question-bank-card
.\dev.ps1 setup
```

`setup` 做三件事：建 `backend\.venv` 虚拟环境、装运行期依赖（锁死版本）、
装开发期工具（ruff / mypy / coverage）和前端工具（eslint / prettier）。

装完先验一下：

```powershell
.\dev.ps1 check
```

它会依次跑**规范检查 → 类型检查 → 三组测试**，全绿才算环境就绪。

---

## 二、日常怎么干活

| 命令 | 干什么 |
| --- | --- |
| `.\dev.ps1 check` | 规范 + 类型 + 三组测试。提交前跑这个。 |
| `.\dev.ps1 test` | 只跑三组测试（后台 2341 条 / 前端 138 条 / 桌面外壳 145 条）。 |
| `.\dev.ps1 lint` | 只跑规范检查（ruff + eslint）。 |
| `.\dev.ps1 format` | 自动修能自动修的（ruff）。改完记得再跑一次 `test`。 |
| `.\dev.ps1 type` | 只跑类型检查（mypy）。 |
| `.\dev.ps1 run` | 起一个本地开发实例（数据落在 `backend\data`，不碰真数据）。 |
| `.\dev.ps1 build` | 打出安装包和免安装包。 |

## 三、提交前必须过的门

一次提交要过下面这几道，全在 CI 上跑，**不过就不算完**：

| 门 | 工具 | 管什么 |
| --- | --- | --- |
| 代码规范 | `ruff check` | 写错的、写漏的、写重复的（Python） |
| 代码格式 | `ruff format --check` | 排版统一（Python） |
| 类型 | `mypy` | 类型对不对、会不会拿 None 直接用（Python） |
| 代码规范 | `eslint` | 没定义就用、声明了没用、循环里的坑（前端） |

**前端的格式不设门禁**：这一版的前端测试大量是「按源码文本断言」的，
一改格式就红一片（实测 50 条）。要格式化单个新文件用
`npm run format -- frontend/你的文件.js`。

## 四、三组测试分别管什么

- **后台**（`backend/core/test_*.py`）：业务逻辑 —— 标签、题库、导出、备份、审核。
- **前端**（`frontend/test_*.js`）：网页行为 —— 标签管理、组卷预览、审核页。
- **桌面外壳**（仓库根目录的 `test_*.py`）：启动器、窗口、凭据、打包、安装。

改动落在哪边，就该有哪边的测试。**新增功能必须补测试**。

## 五、发一个版本

版本号只有一处来源：`backend/core/version.py` 的 `APP_VERSION`。

```powershell
# 1. 改版本号，同时写好 CHANGELOG.md 和 docs/release-notes-v<版本>.md
# 2. 全套检查
.\dev.ps1 check
# 3. 打包
.\dev.ps1 build -Version 1.26.26
# 4. 提交、打标签（标签会触发 CI 自动打包并发 Release）
git commit -am "1.26.26：这一版做了什么"
git tag v1.26.26
git push origin main
git push origin v1.26.26
```

CI 看到 `v*` 标签会自动：装 Inno Setup → 跑全套检查 → 打包 → 审计 →
建 Release → 传附件。**本地不用手动发 Release 了。**

打包脚本 `packaging/build.ps1` 会检查 `version.py` 里的版本号和 `-Version`
参数对不对得上，对不上直接停手 —— 免得装出一个版本号是假的包。

## 六、几条规矩

1. **不要提交真实试卷、数据库、备份、日志、API 凭据或能认出个人的信息。**
   `.gitignore` 已经挡掉大部分，但自己再看一眼 `git status`。
2. **新增功能补测试。**
3. **本地服务只监听回环地址**（127.0.0.1），除非同时实现并验证了完整的认证授权。
4. **提交即表示你有权贡献这些代码**，并同意按 `AGPL-3.0-only` 发布。

## 七、更多

- 开发环境详解（每个工具干什么、为什么这么配、常见坑）：[docs/开发环境.md](docs/开发环境.md)
- 标签系统的设计约定：看 `AGENTS.md`
- 类型检查还没覆盖的模块和逐条问题：[docs/类型检查待办.md](docs/类型检查待办.md)
