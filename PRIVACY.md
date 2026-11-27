# Privacy Policy / 隐私政策

题有据（TiYouJu）是一款本地优先的 Windows 桌面与离线题库工具。所有题库数据、原卷图片/PDF、公式编辑草稿、本地备份与 API 凭据均保存在用户自己的电脑本地磁盘上。

## 1. 不收集遥测与个人数据 / No Telemetry or Tracking

- 本软件**不包含**任何隐藏遥测、行为埋点、崩溃日志自动上传、广告或第三方统计 SDK。
- 不会在未经用户明确操作的情况下扫描或上传用户的本地题库、试卷原件或个人信息。

## 2. 网络请求说明 / Network Connections

仅在以下场景下，软件会通过 HTTPS 发起网络请求：

1. **版本更新检查与安装包下载（默认可关闭）**
   - 启动时若开启了「自动检查更新」（可在 **设置 → 关于与更新** 关闭），会向官网 `https://tiyouju.com/api/latest-release` 发起只读 `GET` 请求获取最新版本号与更新说明，**不携带任何用户身份、设备指纹、题库或本地数据**。
   - 仅当用户手动点击「检查更新 / 下载并安装」时，才会从 `https://tiyouju.com/download/` 下载经官网 `SHA256SUMS.txt` 校验的安装包。
2. **用户主动触发的第三方 AI / OCR / MinerU 服务**
   - 仅在用户自行配置服务凭据（本地通过 Windows DPAPI 加密存储）并明确授权当前云处理、切图识读或 AI 辅助任务时，才会将本次选定的文档页面或单个题目截图发送至用户指定服务商。
3. **用户主动使用的题有据社区（`https://community.tiyouju.com/`）**
   - 仅当用户明确登录社区账号后，才会浏览、下载或打包上传自己选择公开分享的题包。

## 3. 联系方式 / Contact

如有隐私或安全疑问，请在公开仓库提交 Issue 或参阅安全说明：

- Issues: https://github.com/CEHNICA/question-bank-card/issues
- Security Policy: [SECURITY.md](SECURITY.md)
