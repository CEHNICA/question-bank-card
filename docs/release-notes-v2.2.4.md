# 题有据 2.2.4 更新说明

## 升级与改进

1. **OneNote 桌面端唤起与防卡死加固（`onenote_bridge.ps1`、`library_onenote.py`）**
   - **精准识别 `Framework::CFrame` 主窗口**：通过 Win32 `EnumWindows` + `IsWindowVisible` 定位真正的可见 OneNote 主窗口，不再被输入法小窗或启动恢复弹窗的 `MainWindowHandle` 误导。
   - **自动清理无头进程与恢复询问阻塞**：在创建、跳转或协议唤起前，若不存在可见主窗口，自动归零 `ConsecutiveBootCrashes` / `ConsecutiveEarlyCrashes` 异常启动标记、清理卡在后台的无窗口或 `/hyperlink` 僵尸进程并先拉起正常桌面主窗口，避免单实例锁冲突导致打不开。
   - **修正未唤起成功时的状态返回**：在 `shell_open_onenote` 前置保障主窗口，并在 `_refine_open` 校验可见窗口或协议唤起是否实际成功。

2. **工作台与录题界面视觉与交互升级（`eagle/index.html`、`index.html`、`styles.css`）**
   - **Eagle 工作台 `Ctrl + 滚轮` 缩放与顶栏精简**：移除顶栏 `A− 100% A+` 按钮组并改为全局 `Ctrl + 滚轮`（带触控板增量平滑与原卷对比视口 `#opViewport` 避让），统一三栏顶栏高度并新增右侧「题目属性」折叠竖条导轨、区分空库引导卡片与筛选无匹配提示。
   - **左上角 `≡` 主菜单层级重整**：按「录入主入口 / 专题视图 / 题库管理 / 视图与界面 / 系统设置」重排分层，修复条件隐藏项优先级并收紧视口高度适配。
   - **录题首页视觉升级**：补充顶栏品牌标识，居中对齐空态引导卡片以消除多余外层滚动条，重排主次按钮与三步引导卡片并优化左侧空列表视觉反馈。

3. **精简备份策略为按需手动导出与恢复（`library_transfer.py`、`features.py`、`tiyouju_cli.py`、`library-transfer.js`）**
   - **下线后台自动备份开关与定时任务**：关闭 `auto_backup` 并移除 `/api/backup/auto*` 接口与后台轮询，避免长期后台写入占用磁盘；保留完整的手动整库/按卷/按文件夹备份导出（CLI 统一通过 `--dest` 指定保存目录）及只增不减的合并恢复功能。
