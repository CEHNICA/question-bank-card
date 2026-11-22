## 题有据 2.2.5 更新说明

### 更新亮点

1. **启动与首页版本号直观展示**：
   - 桌面启动画面在“题有据”标题旁直接展示当前版本号（如 `v2.2.5`），一打开软件即可确认当前版本。
   - 题库工作台（`/eagle`）左栏底部与左上角 `≡` 应用主菜单底栏、录题首页（`/`）顶栏品牌区同步显示当前版本号，点击工作台版本徽标可直达「设置 → 关于」。

2. **仿 egolite 的多 AI Agent 技能与命令行自动接管**：
   - 安装器（`TiYouJu-Setup`）安装完成后自动同步持久化技能目录（`%LOCALAPPDATA%\QuestionBankCard\skills\tiyouju`），并自动链接到本机主流 AI Agent 技能目录（`.agents/skills`、`.gemini/antigravity/skills`、`.gemini/config/skills`、`.claude/skills`、`.codex/skills`、`.cursor/skills` 等），同时将 `tiyouju.exe` 目录自动加入当前用户 `PATH`。
   - 新增 `tiyouju skill` 命令输出与本机安装版本完全一致的 AI Agent 接管指南（以 `[tiyouju:skill] end of guide` 结尾），并新增 `tiyouju setup-agents` 命令及 `skills/tiyouju/agents/openai.yaml` 元数据。
