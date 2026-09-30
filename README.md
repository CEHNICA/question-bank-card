<p align="center">
  <img src="docs/assets/brand/logo-lockup.svg" width="420" alt="题有据">
</p>

<h1 align="center">题有据</h1>

<p align="center"><strong>每一道题，都能回到原卷。</strong></p>

<p align="center">
  面向 Windows 的本地试题整理工具：把 PDF、Word 和手机照片变成可逐题核对、可追溯入库、可搜索组卷的题库。
</p>

<p align="center">
  <a href="https://github.com/CEHNICA/question-bank-card/releases/latest"><strong>下载最新版</strong></a>
  ·
  <a href="#三步完成一次整理">查看使用流程</a>
  ·
  <a href="#api-与隐私边界">了解隐私边界</a>
</p>

<p align="center">
  <a href="https://github.com/CEHNICA/question-bank-card/releases/latest"><img alt="GitHub Release" src="https://img.shields.io/github/v/release/CEHNICA/question-bank-card?display_name=tag&sort=semver"></a>
  <a href="https://github.com/CEHNICA/question-bank-card/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/CEHNICA/question-bank-card/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="Windows 10 / 11" src="https://img.shields.io/badge/Windows-10%20%2F%2011-16756f">
  <a href="LICENSE"><img alt="License: AGPL-3.0-only" src="https://img.shields.io/badge/license-AGPL--3.0--only-7a6846"></a>
</p>

![题有据逐题核对工作台](docs/assets/screenshots/review-workspace.webp)

> **先说清楚：** 题有据用 AI 加快解析、誊录和复核，但不会宣称自动识别百分之百正确。题目只有经过使用者对照原卷确认，才会进入正式题库。处理新资料时会调用你自行配置的第三方 API；你的数据库、私人原卷、日志和凭据不会随源码发布。

## 三步完成一次整理

| 1. 导入原卷 | 2. 逐题核对 | 3. 确认入库 |
| --- | --- | --- |
| 拖入 PDF、Word 或手机照片；长教材会在本机分片后解析。 | 左边看原卷，右边看识读文字；改字、调范围、配图都在一张题卡里完成。 | 人工确认后生成可追溯版本，在正式题库中搜索、选题和组卷。 |

`导入原卷` → `AI 解析与双读` → `逐题对照确认` → `正式入库与组卷`

## 为什么是“题有据”

- **文字有据：** 原卷截图、识读结果与人工修订并排保留，发现问题可以直接回看来源。
- **入库有门槛：** AI 负责建议和提速，最终确认权始终在使用者手中。
- **配图有检查：** 本机规则复用已有识读结果检查多余图和可能漏图，不额外增加模型调用；明确冲突会阻止直接通过。
- **长资料可续跑：** 教材和讲义按页分片，保留原始页码；失败分片可以单独重试，不必整本重来。
- **两种引擎互证：** 视觉模型的誊录要么与 MinerU 自己识别的文字逐字一致，要么交给复核模型再读、逐处比对；模型“顺手改正”原卷错字、漏字的情况会被发现。
- **审核更顺手：** `J` / `K` 切换题卡，`Enter` 通过并前进，`Space` 打开整题对照；原卷与题面都支持左键拖动和 `Ctrl` + 滚轮缩放。
- **本地边界明确：** 服务默认只监听 `127.0.0.1`；API 凭据由 Windows DPAPI 加密，不进入网页进程、仓库或题库备份。

## 看看实际界面

以下画面使用虚构演示资料，不包含真实学生信息、私人试卷或 API 凭据。

想自己试一遍，可以下载仓库内的 [原创三页演示卷](docs/demo/tiyouju-demo-paper.pdf)；生成方法与字体许可证见 [演示资料说明](docs/demo/README.md)。

### 一题一卡，原卷与文字放在一起核对

![逐题核对工作台：左侧原卷，右侧识读文字](docs/assets/screenshots/review-workspace.webp)

### 整题适屏，也能拖动和缩放看细节

![放大对照：原卷与题面并排显示](docs/assets/screenshots/compare-view.webp)

### 审核完成后，进入正式题库与试题篮

![正式题库、搜索与试题篮](docs/assets/screenshots/library-and-basket.webp)

### 选题后预览并打印试卷

![组卷与打印预览](docs/assets/screenshots/paper-preview.webp)

## 安装与使用

### 推荐：Windows 安装版

1. 打开 [Releases](https://github.com/CEHNICA/question-bank-card/releases/latest)，下载最新的 `TiYouJu-Setup-*.exe`。
2. 完成安装后，从桌面或开始菜单打开“题有据”。
3. 首次处理资料时，按提示填写自己的 API 凭据。
4. 上传资料，等待处理完成，再逐题对照原卷确认。

> 当前发布包可能尚未进行商业代码签名，Windows SmartScreen 因此可能显示风险提醒。请只从本仓库的 Releases 下载，并用发布页提供的 SHA-256 校验值核对文件。

### 从源码运行

需要 Windows 10/11 x64、Python 3.12；Node.js 仅用于运行前端测试。

```powershell
git clone https://github.com/CEHNICA/question-bank-card.git
cd question-bank-card
py -3.12 .\start_question_bank.py
```

首次运行会创建本地虚拟环境并安装锁定依赖。服务随后在 `http://127.0.0.1:8768` 打开。

## 支持的工作方式

- PDF、Word 和多张图片导入；界面聚焦时也可以复制文件后直接粘贴上传。
- PDF 单次解析遵守 MinerU 的 600 页上限；教材/讲义默认按 100 页在本机稳定分片，超长试卷按硬上限分片。
- 任务可以重命名并同步更新题目来源；失败任务可以删除，失败分片可以续跑。
- MinerU、MiniMax 和硅基流动均支持最多 8 个账号组成的本机账号池。
- 多个 MinerU 账号可并行解析不同分片；图像模型账号可同时承载多个识读请求（MiniMax 默认每个账号 6 个，遇到限流会自动减少），多个账号叠加。
- 每道题先用 MinerU 自己识别的文字做独立旁证：与视觉模型逐字一致就不再重复识读；有出入时再请复核模型，并用 MinerU 文字逐处裁定分歧。
- 正式题库支持搜索、试题篮和组卷打印。

## API 与隐私边界

题有据是本地应用，但**处理新资料并非完全离线**。请在上传前了解资料会发送到哪里。

| 服务 | 用途 | 是否必需 |
| --- | --- | --- |
| MinerU | 解析上传的原卷；长资料先在本机分片，再发送相应分片 | 处理新原卷时需要 |
| MiniMax | 对切出的题目截图进行识读 | 处理新题时需要 |
| 硅基流动 | 提供第二路识读结果，用于交叉复核 | 可选 |

- “忽略手写”是一项识读要求，不会在发送前擦除姓名、答案或批改痕迹。处理含有个人信息的材料前，请先取得适当授权，并核对各 API 服务的数据政策。
- API 凭据使用 Windows DPAPI 加密，只能由当前 Windows 用户解密；网页进程只知道“是否配置”和账号数量，不会收到密钥原文。
- 在“配置 API”中用英文分号 `;` 分隔同类凭据。无效、过期或额度耗尽的账号会在本次运行中隔离；限流账号会进入冷却，由其他可用账号接手。
- 服务默认只监听 `127.0.0.1`。当前版本没有面向公网的账号、认证和权限系统，请勿直接暴露到局域网或互联网。
- 用户数据库、原卷、日志和备份属于私密资料。它们已被 `.gitignore` 排除，但提交或公开分发前仍应自行复核。

源码运行时，数据位于仓库的 `backend/` 下；安装版数据位于 `%LOCALAPPDATA%\QuestionBankCard\`。卸载程序不会自动删除用户数据。

## 开发与测试

<details>
<summary>展开完整测试命令</summary>

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

</details>

如需让 MiniMax 充当辅助测试员，请使用 [MiniMax 辅助只读测试指南](docs/MINIMAX_READONLY_TESTING.md)。该流程固定为项目工作区零写入，不调用项目 API，也不能代替人工对照原卷终审。

<details>
<summary>展开 Windows 安装包构建说明</summary>

安装 Inno Setup 7 后，在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1 -Version 1.5.0
```

完整说明见 [packaging/README.md](packaging/README.md)。构建流程会审计最终目录；如发现数据库、原卷、日志、备份或凭据，会立即失败。

</details>

欢迎提交 Issue 和 Pull Request。请先阅读 [参与贡献说明](CONTRIBUTING.md)；报告安全问题请按 [SECURITY.md](SECURITY.md) 私下联系。不要在 Issue、截图或测试样本中附带真实试卷、学生信息、数据库、日志或 API 密钥。

## 许可证

第一方源代码采用 [GNU Affero General Public License v3.0](LICENSE)，SPDX 标识为 `AGPL-3.0-only`。Copyright © 2026 CEHNICA and contributors.

第三方组件保留各自许可证；摘要见 [第三方组件说明](packaging/THIRD_PARTY_NOTICES.txt)，完整文本见 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES/README.md)。其中 PyMuPDF 采用 AGPLv3 / Artifex 商业双许可，KaTeX 采用 MIT 许可证。

本项目按“原样”提供，不附带任何担保。本说明不构成法律意见。
