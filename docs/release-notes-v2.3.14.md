## 题有据 2.3.14 更新说明

### 更新亮点

1. **修复社区题包上传「社区登记题包失败」**：
   - 修复桌面端向资源社区发起登记（`/api/packs/init`）、刷新角色（`/api/me`）和上传题包文件（`PUT /api/packs/:id/bundle`）时未带官方客户端 `User-Agent` 导致被 Cloudflare 返回 403 拦截的问题，同时完善网关错误详情透传。
2. **修复经典页更新进度轮询校验头**：
   - 为经典界面与独立设置页读取 `/api/update/progress` 补齐 `X-QB-Request` 请求头，确保升级进度条正常刷新。
3. **SignPath 开源代码签名策略与自动化预接入**：
   - 补齐 `CODE_SIGNING_POLICY.md`、`PRIVACY.md` 及仓库签名步骤支持（待 SignPath Foundation 凭据配置后自动启用）。

### 验证范围

- `ruff check`、`ruff format --check`、`npm run lint`、根目录 `unittest`、Django `core` 单元测试与全部前端 `node --test` 用例通过。
