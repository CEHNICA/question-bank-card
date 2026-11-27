## 题有据 2.3.10 更新说明

### 更新亮点

1. **国际与国内 AI Agent 自动发现技能、命令行与 MCP**：
   - 安装器内置的 `tiyouju setup-agents` 现支持自动探测本机 **Trae 国内版（`.trae-cn`）、Qwen Code、Qoder、通义灵码、CodeBuddy、Kimi、iFlow、OpenClaw、Cline、Roo Code、Kilo Code** 以及 Cursor、Claude、Codex、Antigravity、Windsurf 等目录，自动完成 Skill 链接、用户 `PATH` 写入及 stdio MCP 安全注册（已有自定义项不覆盖，畸形 JSON 安全跳过）。
   - 新增离线命令 `tiyouju mcp-config`，直接输出适用于 Cherry Studio / Chatbox 等客户端的标准 `mcpServers` JSON、Codex CLI 接入命令及本机客户端状态检查。
2. **右下角版本更新悬浮弹窗**：
   - 当官网 `tiyouju.com` 发布新版本时，打开工作台或录题页将在右下角主动弹出悬浮更新卡片，直接展示版本升级对比与本次更新亮点，支持一键备份并静默安装重启或稍后提醒；并修复工作台「偏好设置 → 关于」中的“自动检查更新”开关同步与菜单徽标唤起更新弹窗。

### 验证范围

- `ruff check`、`ruff format --check`、`mypy`、`npm run lint`、前端 `node --test`、根目录与 Django `core` 单元测试通过。
