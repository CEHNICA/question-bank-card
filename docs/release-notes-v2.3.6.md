## 题有据 2.3.6 更新说明

### 更新亮点

1. **题包社区上线与桌面端双向打通（`https://community.tiyouju.com/`）**：
   - `/eagle` 题库新增结构化题包导出与直达社区入口（默认连接 `https://community.tiyouju.com/`），自定义 HTTPS 域名自动通过系统浏览器打开，支持将社区题包导回本机题库；
   - 支持免费分享及原创题包站外收款与兑换码验证（平台不代收任何款项）。
2. **Cloudflare 免费额度防护与流式直传改造（`community/`）**：
   - 采用两步流式直传 R2 存储题包，避免 Workers 内存上限溢出；
   - 增加 R2 8500 MB 容量硬顶、每日每用户 10 次上传限流、定时清理以及 Turnstile 密钥/令牌首尾空白裁剪与错误码记录。
3. **题有据官网与下载页发布（`https://tiyouju.com/`）**：
   - 同步上线官网介绍页与下载入口，展示真实工作流与核心界面截图，并与社区页面相互链接。

### 验证范围

- `ruff check` / `ruff format --check`、`npm run lint`、`mypy` 全部通过；
- 桌面与打包测试 `unittest`、Django `core` 测试套件、前端 `node --test frontend/test_*.js` 及社区 `typecheck` / `npm test`（35 项）全部通过；
- PyInstaller 冻结产物与 Inno Setup 安装包经 GitHub Actions 自动审计与 SHA-256 校验。
