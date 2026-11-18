# 题有据 2.1.2 更新说明

## 修复与改进

1. **修复其他电脑安装后任务栏显示浏览器图标的问题**
   - 桌面启动器（`app_window.py`）通过 Win32 `SHGetPropertyStoreForWindow` 为 Edge/Chrome `--app` 应用窗口直接写入独立的 `AppUserModelID`（`QuestionBank.CardReview`）与 `RelaunchIconResource` / `RelaunchDisplayNameResource` / `RelaunchCommand`，避免在 Windows 10/11 任务栏与普通 Edge 图标混淆或被接管。
   - 任务栏守护线程新增窗口大小图标状态监测（`WM_GETICON`），即使新机冷启动超过 2 秒或页面在 `/`、`/eagle`、`/library` 之间切换后被浏览器重置，也会自动补刷恢复「题有据」图标，并缓存 GDI 图标句柄防止泄漏。
   - 进程定位改用原生 Win32 进程快照 + `NtQueryInformationProcess` 命令行读取（保留安全 PowerShell 字面量子串回退），兼容含短路径（`ADMINI~1`）或 `[` `]` 通配符字符的用户目录；同时增加 `shutil.which` 兜底查找 Edge/Chrome。
   - 后端补齐 `/favicon.ico`（返回多尺寸 `assets/app.ico`）与全部 HTML 页面根路径图标引用，统一 `practice.html` 品牌图标为 `/app-icon-256.png`。

2. **修复打开已关联 OneNote 笔记时等待条消失的问题**
   - 在题库工作台（`frontend/eagle/index.html`）的 `openOneNotePage` 中恢复 `#onenoteBusyBar` 顶部等待进度条与按钮「正在打开…」忙态锁定，并配合 `/api/library/<id>/onenote/open?wait=1`（`backend/core/library_onenote.py`）同步等待 OneNote COM/协议唤醒结束后再收起等待条，避免刚点下就提前消失。
