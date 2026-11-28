# Code signing policy

## 当前接入状态（2026-11-28）

已实测 GitHub 发布的 `TiYouJu-Setup-2.3.16.exe`：SHA-256 与发布清单一致，Authenticode 状态为 `NotSigned`。仓库的 Actions Secrets 和 Variables 当前均未配置签名参数；此前发布日志显示跳过签名。以下为后续正式版本的签名策略，不代表历史版本已签名或已获得 SignPath 审核通过。

正式发布缺少签名配置时立即停止；签名完成后必须校验两个主程序及安装包的受信任签名、时间戳与一致的发布者证书，通过后才生成公开校验清单和上传文件。该要求不禁止本地构建测试包，但测试包不得冒充已签名的正式发行版。

## 发布者接入步骤

1. 仓库所有者通过 [SignPath Foundation 申请入口](https://signpath.org/apply)申请免费开源签名，阅读[资格及使用条款](https://signpath.org/terms)。申请资格、审核结果与等待时间由服务商决定，不保证获批。
2. 按服务商要求启用账户多因素认证、验证公开仓库控制权，并配置 GitHub 构建来源校验、签名项目及人工批准的正式签名策略。服务端产物配置须允许分别签署包含两个自有 EXE 的应用产物和独立的 Inno Setup 安装包；不替第三方依赖重新签名。
3. 获批后在本仓库 **Settings → Secrets and variables → Actions → Repository secrets** 保存 `SIGNPATH_API_TOKEN`、`SIGNPATH_ORG_ID`。只在服务商和 GitHub 的安全输入框中填写，不把真实令牌放进代码、日志或聊天。
4. 项目和策略默认标识分别为 `question-bank-card`、`release-signing`。服务端名称不同则设置 Actions Variables `SIGNPATH_PROJECT_SLUG`、`SIGNPATH_POLICY_SLUG`；也兼容同名 Repository Secrets。必须使用获批的正式签名策略和受 Windows 信任的证书，自签名或测试证书不满足要求。
5. 发布新版本，按 SignPath 要求批准签名请求。流程先签 `QuestionBankCard.exe` 和 `tiyouju.exe`，将签名程序打进安装包，再签安装包，最终校验证书、时间戳并重新生成 SHA-256。旧版安装包不会因开通服务而自动获得签名。
6. 在独立 Windows 电脑从官方渠道重新下载，检查文件属性中的数字签名和实际安装行为。SmartScreen 还依赖文件及发布者信誉，签名不保证所有设备立即免提示，详见 [Microsoft 官方说明](https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/)。如仍提示，应向微软提交确切发行文件复核，而非关闭防护或移除下载标记。

获批并实际启用 Foundation 证书后，按条款在主页与下载页展示以下归属声明：

> Free code signing provided by SignPath.io, certificate by SignPath Foundation.

## Team roles

- **Committers and Reviewers**: [CEHNICA](https://github.com/CEHNICA)
- **Approvers**: [CEHNICA](https://github.com/CEHNICA)

All code changes are hosted in the public GitHub repository `https://github.com/CEHNICA/question-bank-card`, and only repository-approved release tags (`v*`) built on GitHub-hosted runners may trigger a signing request.

## Signing scope and provenance

- **Signed artifacts** (built exclusively by `.github/workflows/release.yml` on GitHub-hosted `windows-latest` runners):
  - `QuestionBankCard.exe`
  - `tiyouju.exe`
  - `TiYouJu-Setup-<version>.exe`
- **Unsigned third-party runtime files**: Bundled Python standard library, wheel runtime dependencies under `_internal/`, and source/license/skill zip archives are included unmodified under their respective open-source licenses and are not signed by SignPath.

## Privacy policy

See [PRIVACY.md](PRIVACY.md) for how TiYouJu handles local data and explicit network requests.
