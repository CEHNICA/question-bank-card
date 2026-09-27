# 题库题卡版

一个面向 Windows 的本地题库审核工具：上传 PDF、Word 或手机照片后，程序会切分题目、调用 AI 誊录与复核，再让使用者逐题对照原卷确认，最后生成可追溯的正式题库版本。

> 当前仓库公开的是源代码，不包含任何用户数据库、原卷、日志、备份、API 凭据或预构建安装包。

## 主要功能

- 以“一题一卡”的方式对照原卷和识读文字，支持改字、调整题目范围和配图。
- 本机规则复用现有识读结果检查多余图和漏图：不增加模型调用；明确漏图或配图冲突会阻止通过，人工可确认无图并留下可追溯记录。
- 放大对照默认整题适屏，原卷和题面都可用鼠标左键拖动；支持 `Ctrl` + 滚轮缩放。
- `J` / `K` 切换题卡，`Enter` 通过并跳到下一题，`Space` 打开放大对照。
- 独立的正式题库页面，支持搜索、试题篮和组卷打印。
- PDF 单次解析严格遵守 MinerU 的 600 页硬上限；教材/讲义会先在本机按每 100 页稳定分片，超长试卷按硬上限分片，合并后仍保留原书页码与完整来源映射。
- 解析失败的分片可单独续跑；旧版中失败的教材任务可点“按教材重试”，程序会自动补建 100 页分片。失败任务可删除，任务名称可修改并同步到题目来源。
- MinerU、MiniMax 和硅基流动均支持最多 8 个账号的本机账号池。多个 MinerU 账号可并行解析不同分片，多个图像模型账号可并行识读题卡；每个账号仍严格限制为单并发。
- Windows 桌面窗口、开始菜单入口、加密保存 API 凭据和一致性备份。
- 桌面构建带私有数据审计：检测到数据库、原卷、日志、备份或凭据时自动失败。

## 运行要求

- Windows 10/11 x64
- Python 3.12
- Node.js（仅运行前端测试时需要）
- 上传并处理新卷需要 MinerU Token 和 MiniMax API Key；硅基流动 API Key 可选

## 快速开始

1. 下载或克隆本仓库。
2. 双击 `启动题库题卡版.cmd`。
3. 首次启动会创建本地虚拟环境并安装锁定依赖。
4. 按提示配置 API；随后页面会在 `http://127.0.0.1:8768` 打开。
5. 如需桌面入口，完成首次启动后双击 `创建桌面图标.cmd`。

也可以在 PowerShell 中运行：

```powershell
py -3.12 .\start_question_bank.py
```

## API 与隐私边界

- 上传新资料时，原卷会整份或按上述本地分片发送给 MinerU；切出的题目截图会发送给 MiniMax，配置硅基流动后还会发送给硅基流动。分片不会改写本机原文件。
- “忽略手写”只是识读要求，不会在上传前擦除姓名、答案或批改痕迹。处理可识别个人的材料前，请先取得授权并核对各服务的数据政策。
- API 凭据使用 Windows DPAPI 加密，只能由当前 Windows 用户解密。凭据不会写入仓库或题库备份。
- 在“配置 API”中用英文分号 `;` 分隔同类凭据。无效、过期或额度用完的账号会在本次运行中隔离，限流账号会冷却并立即让其他账号接手。只配置一个账号时仍保持串行。
- 解密后的账号池只传给后台工作进程；网页进程只知道“是否配置”和账号数量，不会收到密钥原文。
- 服务默认只监听 `127.0.0.1`。当前版本没有多人账号和公网权限系统，请勿直接暴露到局域网或互联网。
- 用户数据库、原卷、日志与备份属于私密资料；这些路径已被 `.gitignore` 排除，但公开前仍应自行复核。

## 数据与备份

源码运行时，数据位于仓库的 `backend/` 下；安装版的数据位于 `%LOCALAPPDATA%\QuestionBankCard\`。卸载安装版不会自动删除用户数据。

关闭程序后可双击 `备份题库.cmd`。备份包含数据库和原卷，应按私密资料保存。

## 运行测试

```powershell
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock.txt
backend\.venv\Scripts\python.exe -m unittest test_launcher test_backup test_app_window test_credential_dialog test_packaging
Push-Location backend
..\backend\.venv\Scripts\python.exe manage.py test core
Pop-Location
node --test frontend\test_*.js
```

测试使用虚构数据和模拟响应，不会调用付费模型。

## 构建 Windows 安装包

安装 Inno Setup 7 后，在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1 -Version 1.1.0
```

构建说明见 [packaging/README.md](packaging/README.md)。安装包会包含项目许可证、源码地址以及 [完整第三方许可证目录](THIRD_PARTY_LICENSES/README.md)。公开分发自行构建的二进制前，请完整阅读对应源码要求。

## 参与贡献

欢迎提交 Issue 和 Pull Request。提交前请运行完整测试，且不要附带真实试卷、数据库、日志、API 密钥或其他个人信息。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

第一方源代码采用 [GNU Affero General Public License v3.0](LICENSE)，SPDX 标识为 `AGPL-3.0-only`。Copyright © 2026 CEHNICA and contributors.

第三方组件保留各自许可证；摘要见 [packaging/THIRD_PARTY_NOTICES.txt](packaging/THIRD_PARTY_NOTICES.txt)，完整文本见 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES/README.md)。其中 PyMuPDF 采用 AGPLv3 / Artifex 商业双许可，KaTeX 采用 MIT 许可证。

本项目按“原样”提供，不附带任何担保。本说明不构成法律意见。
